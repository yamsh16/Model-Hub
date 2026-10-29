"""One place that decides "does this row belong to the signed-in user?"

Every lookup of a user's data goes through `owned()`. A row that doesn't exist and a row that
belongs to someone else give the same 404, so ids can't be probed to learn what exists.
"""
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import User


def parse_id(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError):
        return None


def owned(db: Session, cls, raw_id: str, user: User, what: str, *extra):
    """Fetch `cls` row `raw_id` only if it belongs to `user` (plus optional extra filters)."""
    row_id = parse_id(raw_id)
    row = None
    if row_id is not None:
        row = db.scalar(select(cls).where(cls.id == row_id, cls.user_id == user.id, *extra))
    if row is None:
        raise HTTPException(404, f"{what} not found")
    return row
