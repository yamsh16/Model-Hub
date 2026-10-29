"""Shared test setup: a fresh in-memory SQLite database per test, and a signed-in test user.

Most tests exercise one feature and don't care who is signed in, so `current_user` is
overridden to return a fixed user. Modules that test sign-in or isolation themselves
(test_auth, test_isolation) opt out and use real tokens.
"""
import os

# Set before the app is imported so no development key files are created during tests.
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-not-for-production-0123456789abcdef")
os.environ.setdefault("ENCRYPTION_KEY", "7bfKuE9llX3-jwuKGcwNfoJgxYlT8ITF7au1vEI97pU=")  # a throwaway key, tests only

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import auth
import db
import models
from main import app

REAL_AUTH_MODULES = {"test_auth", "test_isolation"}


@pytest.fixture(autouse=True)
def database(request):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk_on(conn, _):  # SQLite ignores foreign keys unless asked
        conn.execute("PRAGMA foreign_keys=ON")

    db.Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[db.get_db] = override_db
    auth.throttle = auth.LoginThrottle()

    if request.module.__name__ not in REAL_AUTH_MODULES:
        with Session(engine) as s:
            test_user = models.User(email="tester@example.com", password_hash="unused")
            s.add(test_user)
            s.commit()
            test_user_id = test_user.id

        def fake_current_user():
            with Session(engine) as s:
                return s.get(models.User, test_user_id)

        app.dependency_overrides[auth.current_user] = fake_current_user

    request.node.engine = engine
    yield engine
    app.dependency_overrides.clear()
