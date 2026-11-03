"""Upload, scan, list and delete model weight files (safetensors and GGUF only).

The upload is the raw request body (not multipart) so it can be streamed to disk without
holding it in memory, and refused as soon as it exceeds a limit. The file is written under a
name we generate, scanned, and only then moved into the user's folder and recorded. Nothing
here loads or runs the model: serving uploaded weights is a later, sandboxed step.
"""
import hashlib
import os
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import config
import scan
from auth import current_user
from db import get_db
from models import Model, User
from ownership import owned
from testsets import safe_filename

router = APIRouter(prefix="/weights", tags=["weights"], dependencies=[Depends(current_user)])

CHUNK_BYTES = 1024 * 1024
STALE_TEMP_SECONDS = 24 * 3600


class WeightOut(BaseModel):
    id: str
    name: str
    filename: str
    format: str
    size_bytes: int
    sha256: str
    tensor_count: int
    parameter_count: int
    dtypes: dict[str, int]
    architecture: str | None
    metadata: dict[str, str]
    warnings: list[str]


def weights_path(user_id: uuid.UUID, model_id: uuid.UUID, fmt: str) -> Path:
    """Where a user's file lives. Built only from ids and a fixed extension, never from anything
    the user typed, so an upload can't choose its own path."""
    return config.upload_dir() / str(user_id) / f"{model_id}.{fmt}"


def _temp_dir() -> Path:
    return config.upload_dir() / "tmp"


def cleanup_stale_temp() -> int:
    """Delete half-written uploads left behind by a crash. Returns how many were removed."""
    removed = 0
    cutoff = time.time() - STALE_TEMP_SECONDS
    for part in _temp_dir().glob("*.part") if _temp_dir().exists() else []:
        try:
            if part.stat().st_mtime < cutoff:
                part.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def _out(row: Model) -> WeightOut:
    s = row.scan
    return WeightOut(
        id=str(row.id), name=row.name, filename=s.get("original_filename", ""), format=row.file_format,
        size_bytes=row.file_size, sha256=row.file_sha256, tensor_count=s["tensor_count"],
        parameter_count=s["parameter_count"], dtypes=s["dtypes"], architecture=s.get("architecture"),
        metadata=s.get("metadata", {}), warnings=s.get("warnings", []),
    )


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _user_bytes(db: Session, user: User) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(Model.file_size), 0)).where(Model.user_id == user.id, Model.kind == "upload")
    )


@router.post("", response_model=WeightOut, status_code=201)
async def upload_weights(
    request: Request,
    name: str = Query(min_length=1, max_length=200),
    filename: str = Query(min_length=1, max_length=255),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    filename = safe_filename(filename, "weights")
    try:
        fmt = scan.format_for_filename(filename)
    except scan.ScanError as e:
        raise HTTPException(422, str(e))

    # Refuse early, from the declared size, before reading any of the body.
    try:
        declared = int(request.headers["content-length"])
    except (KeyError, ValueError):
        raise HTTPException(411, "Content-Length is required")
    limit = config.max_upload_bytes()
    if declared <= 0:
        raise HTTPException(422, "The file is empty")
    if declared > limit:
        raise HTTPException(413, f"File is larger than the {limit // config.GIB} GB limit")
    if _user_bytes(db, user) + declared > config.max_user_upload_bytes():
        raise HTTPException(413, "Your storage quota is full; delete an uploaded model first")
    db.commit()  # don't hold a pooled connection while the (possibly long) upload streams in

    model_id = uuid.uuid4()
    temp = _temp_dir() / f"{model_id}.part"
    final = weights_path(user.id, model_id, fmt)
    temp.parent.mkdir(parents=True, exist_ok=True)
    try:
        digest, written = hashlib.sha256(), 0
        with open(temp, "wb") as out:
            async for chunk in request.stream():
                written += len(chunk)
                if written > limit:
                    raise HTTPException(413, f"File is larger than the {limit // config.GIB} GB limit")
                digest.update(chunk)
                await run_in_threadpool(out.write, chunk)
        try:
            result = await run_in_threadpool(scan.scan_file, str(temp), fmt)
        except scan.ScanError as e:
            raise HTTPException(422, str(e))

        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp, final)
        report = asdict(result) | {"original_filename": filename}
        row = Model(
            id=model_id, user_id=user.id, name=name, kind="upload", file_format=fmt,
            file_size=written, file_sha256=digest.hexdigest(), scan=report,
        )
        db.add(row)
        db.commit()
    except BaseException:
        # Any failure (rejected file, client disconnect, database error): leave no file behind.
        _unlink(temp)
        _unlink(final)
        raise
    return _out(row)


def _upload_rows(user: User):
    return (Model.user_id == user.id, Model.kind == "upload")


@router.get("", response_model=list[WeightOut])
def list_weights(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Model).where(*_upload_rows(user)).order_by(Model.created_at, Model.id))
    return [_out(r) for r in rows]


@router.get("/{weights_id}", response_model=WeightOut)
def read_weights(weights_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _out(owned(db, Model, weights_id, user, "Uploaded model", Model.kind == "upload"))


@router.delete("/{weights_id}", status_code=204)
def delete_weights(weights_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = owned(db, Model, weights_id, user, "Uploaded model", Model.kind == "upload")
    path = weights_path(user.id, row.id, row.file_format)
    db.delete(row)
    db.commit()
    _unlink(path)  # after the commit: a failed delete must not leave a row pointing at no file
