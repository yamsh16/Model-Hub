import ipaddress
import os
import socket
import time
import uuid
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select
from sqlalchemy.orm import Session

import secretbox
from auth import current_user
from db import get_db
from models import Model, User
from ownership import owned

router = APIRouter(prefix="/endpoints", tags=["endpoints"], dependencies=[Depends(current_user)])

PROBE_TIMEOUT_SECONDS = 10.0
EVAL_TIMEOUT_SECONDS = 30.0

class EndpointIn(BaseModel):
    name: str
    url: HttpUrl  # OpenAI-compatible chat completions URL
    model: str
    api_key: str | None = None


class EndpointOut(BaseModel):
    id: str
    name: str
    url: str
    model: str
    latency_ms: int


def _allow_private() -> bool:
    return os.environ.get("ALLOW_PRIVATE_ENDPOINTS") == "1"


def check_url_is_public(url: str) -> None:
    """Reject URLs that resolve to loopback/private/link-local addresses (SSRF guard)."""
    if _allow_private():
        return
    host = urlparse(url).hostname
    if not host:
        raise HTTPException(422, "URL has no host")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise HTTPException(422, f"Could not resolve host '{host}'")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise HTTPException(
                422, "Endpoint resolves to a private or local address, which is not allowed"
            )


def probe(url: str, model: str, api_key: str | None) -> int:
    """Send a 1-token chat request; return latency in ms or raise HTTPException."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    start = time.monotonic()
    try:
        # No redirects: a redirect could point at an internal address we just vetted against.
        r = httpx.post(
            url, json=body, headers=headers, timeout=PROBE_TIMEOUT_SECONDS, follow_redirects=False
        )
    except httpx.TimeoutException:
        raise HTTPException(502, "Endpoint timed out")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Could not reach endpoint: {type(e).__name__}")
    if r.status_code in (401, 403):
        raise HTTPException(400, "Endpoint rejected the API key")
    if not r.is_success:
        raise HTTPException(400, f"Endpoint returned HTTP {r.status_code}")
    return int((time.monotonic() - start) * 1000)


def _chat_request(ep: dict, messages: list[dict], stream: bool, max_tokens: int | None,
                  temperature: float | None) -> tuple[dict, dict]:
    headers = {"Content-Type": "application/json"}
    if ep["api_key"]:
        headers["Authorization"] = f"Bearer {ep['api_key']}"
    body: dict = {"model": ep["model"], "messages": messages}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if temperature is not None:
        body["temperature"] = temperature
    if stream:
        body["stream"] = True
    return headers, body


def _check_status(status_code: int) -> None:
    if status_code in (401, 403):
        raise HTTPException(400, "Endpoint rejected the API key")
    if not 200 <= status_code < 300:
        raise HTTPException(502, f"Endpoint returned HTTP {status_code}")


def chat_messages(ep: dict, messages: list[dict], max_tokens: int | None = None,
                  temperature: float | None = None) -> str:
    """Send a conversation to a registered endpoint and return the full reply text."""
    headers, body = _chat_request(ep, messages, False, max_tokens, temperature)
    try:
        r = httpx.post(
            ep["url"], json=body, headers=headers,
            timeout=EVAL_TIMEOUT_SECONDS, follow_redirects=False,
        )
    except httpx.TimeoutException:
        raise HTTPException(502, "Endpoint timed out")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Could not reach endpoint: {type(e).__name__}")
    _check_status(r.status_code)
    try:
        return r.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(502, "Endpoint reply was not in OpenAI chat-completions format")


def chat_completion(ep: dict, prompt: str, max_tokens: int = 20) -> str:
    """One-shot, deterministic single-message call (used by evaluation)."""
    return chat_messages(
        ep, [{"role": "user", "content": prompt}], max_tokens=max_tokens, temperature=0
    )


def open_chat_stream(ep: dict, messages: list[dict]) -> httpx.Response:
    """Open a streaming chat request; status is checked before returning so errors surface early.

    The caller must close the returned response.
    """
    headers, body = _chat_request(ep, messages, True, None, None)
    client = httpx.Client(timeout=httpx.Timeout(EVAL_TIMEOUT_SECONDS), follow_redirects=False)
    try:
        r = client.send(client.build_request("POST", ep["url"], json=body, headers=headers),
                        stream=True)
    except httpx.TimeoutException:
        client.close()
        raise HTTPException(502, "Endpoint timed out")
    except httpx.HTTPError as e:
        client.close()
        raise HTTPException(502, f"Could not reach endpoint: {type(e).__name__}")
    try:
        _check_status(r.status_code)
    except HTTPException:
        r.close()
        client.close()
        raise
    original_close = r.close

    def close() -> None:
        original_close()
        client.close()

    r.close = close  # type: ignore[method-assign]  # also release the client when the caller closes
    return r


def _endpoint_rows(user: User):
    return (Model.user_id == user.id, Model.kind == "endpoint")


def get_endpoint(db: Session, user: User, endpoint_id: str) -> dict:
    """The signed-in user's endpoint as a plain dict, with the API key decrypted for use."""
    row = owned(db, Model, endpoint_id, user, "Endpoint", Model.kind == "endpoint")
    return {
        "id": str(row.id),
        "name": row.name,
        "url": row.url,
        "model": row.model_name,
        "api_key": secretbox.decrypt(row.api_key_encrypted),
        "latency_ms": row.latency_ms or 0,
    }


def _out(row: Model) -> EndpointOut:
    return EndpointOut(
        id=str(row.id), name=row.name, url=row.url, model=row.model_name,
        latency_ms=row.latency_ms or 0,
    )


@router.post("", response_model=EndpointOut, status_code=201)
def register(body: EndpointIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    url = str(body.url)
    check_url_is_public(url)
    latency = probe(url, body.model, body.api_key)
    row = Model(
        user_id=user.id, name=body.name, kind="endpoint", url=url, model_name=body.model,
        api_key_encrypted=secretbox.encrypt(body.api_key),  # encrypted at rest, never returned
        latency_ms=latency,
    )
    db.add(row)
    db.commit()
    return _out(row)


@router.get("", response_model=list[EndpointOut])
def list_endpoints(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Model).where(*_endpoint_rows(user)).order_by(Model.created_at, Model.id))
    return [_out(r) for r in rows]


@router.get("/{endpoint_id}", response_model=EndpointOut)
def read_endpoint(endpoint_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _out(owned(db, Model, endpoint_id, user, "Endpoint", Model.kind == "endpoint"))


@router.delete("/{endpoint_id}", status_code=204)
def delete_endpoint(endpoint_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(owned(db, Model, endpoint_id, user, "Endpoint", Model.kind == "endpoint"))
    db.commit()
