"""Start, stop and chat with uploaded GGUF models, each in its own sandbox (see sandbox.py).

State lives in memory: which containers this process started, who owns them, when they were
last used. On startup every container the app ever labelled is removed, so after a crash or
restart the registry and Docker agree (nothing is left running and unaccounted for).
"""
import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel
from sqlalchemy.orm import Session

import config
import sandbox
from auth import current_user
from chat import ChatIn, ChatOut, _deltas, _sse
from db import get_db
from models import Model, User
from ownership import owned
from storage import weights_path

log = logging.getLogger("modelhub.serving")
router = APIRouter(prefix="/weights", tags=["serving"], dependencies=[Depends(current_user)])

REAP_INTERVAL_SECONDS = 30
MAX_PARALLEL_CHATS_PER_MODEL = 2


@dataclass
class Running:
    model_id: uuid.UUID
    user_id: uuid.UUID
    name: str  # container name
    template: str
    state: str  # "starting" | "running"
    started: float
    last_used: float
    slots: threading.BoundedSemaphore = field(default_factory=lambda: threading.BoundedSemaphore(MAX_PARALLEL_CHATS_PER_MODEL))


class Manager:
    """Tracks the containers this process started. All decisions are made under one lock;
    slow Docker calls happen outside it."""

    def __init__(self):
        self._lock = threading.Lock()
        self._running: dict[uuid.UUID, Running] = {}

    def get(self, model_id: uuid.UUID) -> Running | None:
        with self._lock:
            return self._running.get(model_id)

    def reserve(self, model_id: uuid.UUID, user_id: uuid.UUID, template: str) -> Running:
        """Claim a slot (state "starting") or refuse. Enforces the per-user and global caps."""
        with self._lock:
            if model_id in self._running:
                return self._running[model_id]
            mine = [r for r in self._running.values() if r.user_id == user_id]
            if len(mine) >= config.serve_max_per_user():
                raise HTTPException(409, "You already have a model running; stop it before starting another")
            if len(self._running) >= config.serve_max_running():
                raise HTTPException(503, "The server is running as many models as it can right now; try again later")
            now = time.monotonic()
            entry = Running(model_id, user_id, sandbox.container_name(model_id), template, "starting", now, now)
            self._running[model_id] = entry
            return entry

    def mark_running(self, model_id: uuid.UUID) -> None:
        with self._lock:
            if model_id in self._running:
                self._running[model_id].state = "running"
                self._running[model_id].last_used = time.monotonic()

    def forget(self, model_id: uuid.UUID) -> Running | None:
        with self._lock:
            return self._running.pop(model_id, None)

    def touch(self, model_id: uuid.UUID) -> None:
        with self._lock:
            if model_id in self._running:
                self._running[model_id].last_used = time.monotonic()

    def idle_models(self, now: float, idle_seconds: float) -> list[Running]:
        with self._lock:
            return [r for r in self._running.values() if r.state == "running" and now - r.last_used > idle_seconds]

    def all(self) -> list[Running]:
        with self._lock:
            return list(self._running.values())


manager = Manager()


def stop_model(model_id: uuid.UUID) -> None:
    """Stop and forget a model's container (safe to call when it is not running)."""
    entry = manager.forget(model_id)
    if entry is not None:
        try:
            sandbox.stop(entry.name)
        except sandbox.SandboxError:
            log.warning("could not stop container %s", entry.name)


def reap_idle() -> int:
    """Stop models nobody has used for SERVE_IDLE_SECONDS. Returns how many were stopped."""
    stale = manager.idle_models(time.monotonic(), config.serve_idle_seconds())
    for entry in stale:
        stop_model(entry.model_id)
    return len(stale)


def shutdown_all() -> None:
    for entry in manager.all():
        stop_model(entry.model_id)


def startup_cleanup() -> None:
    """Remove containers left over from a previous run. Quiet if Docker is not available."""
    try:
        removed = sandbox.stop_all_managed()
        if removed:
            log.info("removed %d leftover model container(s)", removed)
    except sandbox.SandboxError:
        pass


async def reaper_loop() -> None:
    while True:
        await asyncio.sleep(REAP_INTERVAL_SECONDS)
        try:
            await run_in_threadpool(reap_idle)
        except Exception:  # a failed sweep must not kill the loop
            log.exception("idle sweep failed")


# ----------------------------------------------------------------------------------- routes

class ServeIn(BaseModel):
    chat_template: str = sandbox.DEFAULT_TEMPLATE


class ServeOut(BaseModel):
    state: str  # "stopped" | "starting" | "running"
    chat_template: str | None
    memory: str
    cpus: float
    context: int
    idle_stop_seconds: int
    templates: list[str]


def _status(entry: Running | None) -> ServeOut:
    limits = sandbox.limits_from_config()
    return ServeOut(
        state=entry.state if entry else "stopped",
        chat_template=entry.template if entry else None,
        memory=limits.memory, cpus=limits.cpus, context=limits.context,
        idle_stop_seconds=config.serve_idle_seconds(), templates=list(sandbox.CHAT_TEMPLATES),
    )


def _gguf(db: Session, user: User, weights_id: str) -> Model:
    return owned(db, Model, weights_id, user, "Uploaded model", Model.kind == "upload")


def _unavailable(e: sandbox.SandboxError) -> HTTPException:
    return HTTPException(503 if isinstance(e, sandbox.SandboxUnavailable) else 502, str(e))


@router.get("/{weights_id}/serve", response_model=ServeOut)
def serve_status(weights_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = _gguf(db, user, weights_id)
    return _status(manager.get(row.id))


@router.post("/{weights_id}/serve", response_model=ServeOut)
def serve_start(weights_id: str, body: ServeIn | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    body = body or ServeIn()
    row = _gguf(db, user, weights_id)
    if row.file_format != "gguf":
        raise HTTPException(422, "Only GGUF models can be run for now; safetensors files are stored but not served")
    if body.chat_template not in sandbox.CHAT_TEMPLATES:
        raise HTTPException(422, f"Unknown chat template; choose one of: {', '.join(sandbox.CHAT_TEMPLATES)}")
    limits = sandbox.limits_from_config()
    if row.file_size > sandbox.memory_bytes(limits.memory) * 0.8:
        raise HTTPException(422, f"This model is too large for the {limits.memory} memory limit")
    path = weights_path(user.id, row.id, row.file_format)
    if not path.exists():
        raise HTTPException(409, "The model file is missing on the server; upload it again")
    model_id = row.id
    db.commit()  # don't hold a pooled connection while the container starts

    existing = manager.get(model_id)
    if existing is not None:
        if existing.state == "starting":
            raise HTTPException(409, "The model is still starting")
        manager.touch(model_id)
        return _status(existing)

    entry = manager.reserve(model_id, user.id, body.chat_template)
    try:
        sandbox.start(
            entry.name, str(path), limits, body.chat_template,
            {"modelhub.user": str(user.id), "modelhub.model": str(model_id)},
        )
    except sandbox.SandboxError as e:
        manager.forget(model_id)
        raise _unavailable(e)
    except BaseException:
        manager.forget(model_id)
        raise
    manager.mark_running(model_id)
    return _status(manager.get(model_id))


@router.delete("/{weights_id}/serve", status_code=204)
def serve_stop(weights_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = _gguf(db, user, weights_id)
    model_id = row.id
    db.commit()
    stop_model(model_id)


@router.post("/{weights_id}/chat")
def weights_chat(weights_id: str, body: ChatIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = _gguf(db, user, weights_id)
    model_id = row.id
    db.commit()  # the model may take a while; don't pin a database connection

    entry = manager.get(model_id)
    if entry is None:
        raise HTTPException(409, "Start the model first")
    if entry.state != "running":
        raise HTTPException(409, "The model is still starting")
    if not entry.slots.acquire(blocking=False):
        raise HTTPException(429, "The model is busy answering other requests; try again in a moment")

    handed_off = False  # True once the streaming response owns the slot
    try:
        manager.touch(model_id)
        # The caller can't raise the generation limit: it is the server's, to bound how long a
        # request can occupy the container.
        payload = {
            "messages": [m.model_dump() for m in body.messages],
            "max_tokens": config.serve_max_tokens(),
            "stream": body.stream,
        }
        if not sandbox.is_running(entry.name):
            stop_model(model_id)
            raise HTTPException(409, "The model stopped (it may have run out of memory); start it again")

        if not body.stream:
            return ChatOut(reply=sandbox.chat_once(entry.name, payload))

        def events():
            try:
                for text in _deltas(sandbox.chat_stream(entry.name, payload)):
                    manager.touch(model_id)
                    yield _sse({"delta": text})
                yield _sse("[DONE]")
            except sandbox.SandboxError as e:
                yield _sse({"error": str(e)})

        response = StreamingResponse(
            events(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            # Runs when the response ends, including when the client disconnects mid-stream.
            background=BackgroundTask(entry.slots.release),
        )
        handed_off = True
        return response
    except sandbox.SandboxError as e:
        raise _unavailable(e)
    finally:
        if not handed_off:
            entry.slots.release()
