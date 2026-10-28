import logging
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db import get_db
from models import User

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("modelhub.auth")

TOKEN_HOURS = 12
MIN_PASSWORD = 10
MAX_PASSWORD = 128  # bounds hashing work; argon2 is deliberately slow
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_FAILURES = 5  # failed logins per email+IP ...
FAILURE_WINDOW_SECONDS = 15 * 60  # ... within this window, then 429

_hasher = PasswordHasher()
# Verified against when the email is unknown, so "no such user" costs the same time as
# "wrong password" and the response timing doesn't reveal which emails are registered.
_DUMMY_HASH = _hasher.hash("not-a-real-password")

_secret = os.environ.get("JWT_SECRET")
if not _secret:
    # Dev convenience: tokens stop working when the server restarts. Set JWT_SECRET in production.
    _secret = secrets.token_urlsafe(48)
    log.warning("JWT_SECRET is not set; using a random per-process key (sessions end on restart)")


class Credentials(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: str
    email: str


class AuthOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class LoginThrottle:
    """Counts recent failed logins per key (email + client IP). In memory: fine for one process."""

    def __init__(self, max_failures=MAX_FAILURES, window=FAILURE_WINDOW_SECONDS, clock=time.monotonic):
        self.max, self.window, self.clock = max_failures, window, clock
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        cutoff = self.clock() - self.window
        recent = [t for t in self._fails.get(key, []) if t > cutoff]
        if recent:
            self._fails[key] = recent
        else:
            self._fails.pop(key, None)
        return recent

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._recent(key)) >= self.max

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._recent(key)
            self._fails.setdefault(key, []).append(self.clock())

    def clear(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


throttle = LoginThrottle()


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if len(email) > 320 or not EMAIL_RE.match(email):
        raise HTTPException(422, "Enter a valid email address")
    return email


def check_password_rules(password: str, email: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise HTTPException(422, f"Password must be at least {MIN_PASSWORD} characters")
    if len(password) > MAX_PASSWORD:
        raise HTTPException(422, f"Password must be at most {MAX_PASSWORD} characters")
    if password.lower() == email or password.lower() == email.split("@")[0]:
        raise HTTPException(422, "Password must not be your email address")


def make_token(user_id: uuid.UUID) -> str:
    now = datetime.now(timezone.utc)
    claims = {"sub": str(user_id), "iat": now, "exp": now + timedelta(hours=TOKEN_HOURS)}
    return jwt.encode(claims, _secret, algorithm="HS256")


def _user_out(user: User) -> UserOut:
    return UserOut(id=str(user.id), email=user.email)


def _auth_out(user: User) -> AuthOut:
    return AuthOut(access_token=make_token(user.id), user=_user_out(user))


def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    """Resolve the signed-in user from `Authorization: Bearer <token>`, or raise 401."""
    unauthorized = HTTPException(401, "Sign in required", headers={"WWW-Authenticate": "Bearer"})
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthorized
    try:
        # Pin the algorithm: never let the token choose how it is verified.
        claims = jwt.decode(token, _secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
        user = db.get(User, uuid.UUID(claims["sub"]))
    except (jwt.PyJWTError, ValueError):
        raise unauthorized
    if user is None:  # account deleted after the token was issued
        raise unauthorized
    return user


@router.post("/register", response_model=AuthOut, status_code=201)
def register(body: Credentials, db: Session = Depends(get_db)):
    email = normalize_email(body.email)
    check_password_rules(body.password, email)
    user = User(email=email, password_hash=_hasher.hash(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # the case-insensitive unique index on lower(email)
        db.rollback()
        raise HTTPException(409, "That email is already registered")
    return _auth_out(user)


@router.post("/login", response_model=AuthOut)
def login(body: Credentials, request: Request, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    key = f"{email}|{request.client.host if request.client else '?'}"
    if throttle.blocked(key):
        raise HTTPException(429, "Too many failed attempts; try again in a few minutes",
                            headers={"Retry-After": str(FAILURE_WINDOW_SECONDS)})

    user = db.scalar(select(User).where(func.lower(User.email) == email))
    stored = user.password_hash if user else _DUMMY_HASH
    try:
        ok = _hasher.verify(stored, body.password[:MAX_PASSWORD])
    except (VerificationError, InvalidHashError):
        ok = False
    if not ok or user is None:
        throttle.record_failure(key)
        raise HTTPException(401, "Invalid email or password")

    throttle.clear(key)
    if _hasher.check_needs_rehash(user.password_hash):  # parameters were strengthened since signup
        user.password_hash = _hasher.hash(body.password)
        db.commit()
    return _auth_out(user)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return _user_out(user)
