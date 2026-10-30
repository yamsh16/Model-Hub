import json
from typing import Literal

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import endpoints
from auth import current_user
from db import get_db
from models import User

router = APIRouter(prefix="/endpoints", tags=["chat"], dependencies=[Depends(current_user)])

MAX_MESSAGES = 50
MAX_CONTENT_CHARS = 8000


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=MAX_CONTENT_CHARS)


class ChatIn(BaseModel):
    # The server keeps no chat state: the client sends the whole conversation each time.
    messages: list[Message] = Field(min_length=1, max_length=MAX_MESSAGES)
    stream: bool = False


class ChatOut(BaseModel):
    reply: str


def _sse(payload: dict | str) -> str:
    return f"data: {payload if isinstance(payload, str) else json.dumps(payload)}\n\n"


def _deltas(lines):
    """Yield text fragments from an OpenAI-style SSE stream (`data: {...}` lines)."""
    for line in lines:
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            choice = json.loads(data)["choices"][0]
            text = (choice.get("delta") or {}).get("content")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            continue  # keep-alives or vendor-specific chunks we don't understand
        if text:
            yield text


def _stream_reply(upstream: httpx.Response):
    try:
        for text in _deltas(upstream.iter_lines()):
            yield _sse({"delta": text})
        yield _sse("[DONE]")
    except httpx.HTTPError as e:
        yield _sse({"error": f"Stream interrupted: {type(e).__name__}"})
    finally:
        upstream.close()


@router.post("/{endpoint_id}/chat")
def chat(endpoint_id: str, body: ChatIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ep = endpoints.get_endpoint(db, user, endpoint_id)
    db.commit()  # end the read transaction: don't hold a pooled connection while the model replies
    endpoints.check_url_is_public(ep["url"])  # re-check: DNS can change after registration
    messages = [m.model_dump() for m in body.messages]

    if not body.stream:
        return ChatOut(reply=endpoints.chat_messages(ep, messages))

    # Errors (bad key, unreachable) are raised here, before any bytes are streamed,
    # so the client gets a normal HTTP error instead of a half-open event stream.
    upstream = endpoints.open_chat_stream(ep, messages)
    return StreamingResponse(
        _stream_reply(upstream),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
