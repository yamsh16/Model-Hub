"""Fixtures for the end-to-end tests: they talk to a running stack over HTTP, nothing is faked.

`python e2e/run.py` starts the stack and sets the environment. To run against a stack you
started yourself:  E2E_API=http://127.0.0.1:8001 E2E_WEB=http://127.0.0.1:3000 pytest e2e
(the stub model and the restart/hardening tests need run.py's compose files).
"""
import os
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))  # weights_fixtures: pure file builders

API = os.environ.get("E2E_API", "http://127.0.0.1:8001").rstrip("/")
WEB = os.environ.get("E2E_WEB", "http://127.0.0.1:3000").rstrip("/")
COMPOSE = shlex.split(os.environ.get("E2E_COMPOSE", ""))  # empty when not started by run.py
STUB_URL = "http://stub-model:9000/v1/chat/completions"  # as the API container sees it
PASSWORD = "an e2e test password"


def pytest_collection_modifyitems(config, items):
    """Skip everything when no stack is reachable, unless the runner says a stack is required."""
    try:
        httpx.get(f"{API}/health", timeout=3).raise_for_status()
        return
    except httpx.HTTPError:
        if os.environ.get("E2E_REQUIRED"):
            pytest.exit(f"the stack is required but {API} is not answering", returncode=2)
    skip = pytest.mark.skip(reason=f"no stack at {API}; start one with `python e2e/run.py --keep`")
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session")
def http():
    with httpx.Client(timeout=60) as client:
        yield client


class User:
    def __init__(self, http: httpx.Client, email: str, token: str):
        self.http, self.email, self.token = http, email, token
        self.headers = {"Authorization": f"Bearer {token}"}

    def call(self, method: str, path: str, **kw) -> httpx.Response:
        return self.http.request(method, f"{API}{path}", headers={**self.headers, **kw.pop("headers", {})}, **kw)

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, **kw):
        return self.call("POST", path, **kw)

    def put(self, path, **kw):
        return self.call("PUT", path, **kw)

    def delete(self, path, **kw):
        return self.call("DELETE", path, **kw)


def sign_up(http: httpx.Client) -> User:
    email = f"e2e-{uuid.uuid4().hex[:10]}@example.com"
    r = http.post(f"{API}/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    return User(http, email, r.json()["access_token"])


@pytest.fixture
def alice(http):
    return sign_up(http)


@pytest.fixture
def bob(http):
    return sign_up(http)


def compose(*args: str) -> subprocess.CompletedProcess:
    if not COMPOSE:
        pytest.skip("needs the compose files: run through `python e2e/run.py`")
    return subprocess.run([*COMPOSE, *args], capture_output=True, text=True, timeout=180)


def wait_until_healthy(http: httpx.Client, seconds: float = 90) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if http.get(f"{API}/health", timeout=3).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise AssertionError("the API did not come back")
