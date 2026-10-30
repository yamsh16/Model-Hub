import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import auth
import models
from main import app

PASSWORD = "correct horse battery"


client = TestClient(app)


def register(email="Alice@Example.com", password=PASSWORD):
    return client.post("/auth/register", json={"email": email, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_register_login_me_round_trip(database):
    r = register()
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["email"] == "alice@example.com"  # normalised
    assert client.get("/auth/me", headers=bearer(body["access_token"])).json()["email"] == "alice@example.com"

    login = client.post("/auth/login", json={"email": " ALICE@example.com ", "password": PASSWORD})
    assert login.status_code == 200 and login.json()["user"]["id"] == body["user"]["id"]

    with Session(database) as s:  # the password itself is never stored
        row = s.query(models.User).one()
        assert PASSWORD not in row.password_hash and row.password_hash.startswith("$argon2id$")


@pytest.mark.parametrize(
    "email,password",
    [("not-an-email", PASSWORD), ("a@b", PASSWORD), ("a@example.com", "short"),
     ("a@example.com", "x" * (auth.MAX_PASSWORD + 1)), ("alice@example.com", "alice@example.com"),
     ("alice@example.com", "ALICE")],
)
def test_registration_validation(email, password):
    assert register(email, password).status_code == 422


def test_duplicate_email_rejected_ignoring_case():
    assert register("a@example.com").status_code == 201
    assert register("A@EXAMPLE.COM").status_code == 409


def test_bad_logins_look_identical():
    register()
    wrong = client.post("/auth/login", json={"email": "alice@example.com", "password": "wrong password!!"})
    unknown = client.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_login_throttle_blocks_after_repeated_failures_and_resets_on_success():
    register()
    bad = {"email": "alice@example.com", "password": "wrong password!!"}
    for _ in range(auth.MAX_FAILURES):
        assert client.post("/auth/login", json=bad).status_code == 401
    blocked = client.post("/auth/login", json={**bad, "password": PASSWORD})
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers  # even the right password
    # a different account is unaffected
    register("bob@example.com")
    assert client.post("/auth/login", json={"email": "bob@example.com", "password": PASSWORD}).status_code == 200


def test_throttle_expires_and_success_clears_count():
    now = [0.0]
    t = auth.LoginThrottle(max_failures=2, window=60, clock=lambda: now[0])
    t.record_failure("k"); t.record_failure("k")
    assert t.blocked("k")
    now[0] = 61
    assert not t.blocked("k")
    t.record_failure("k"); t.clear("k"); t.record_failure("k")
    assert not t.blocked("k")


@pytest.mark.parametrize("header", [None, "", "Bearer", "Bearer nonsense", "Basic abc", "bearer"])
def test_me_requires_a_valid_bearer_token(header):
    headers = {"Authorization": header} if header is not None else {}
    r = client.get("/auth/me", headers=headers)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


def forge(claims, key=None, alg="HS256"):
    return jwt.encode(claims, key or auth._secret, algorithm=alg)


def test_tokens_that_must_be_rejected(database):
    uid = register().json()["user"]["id"]
    now = datetime.now(timezone.utc)
    good = {"sub": uid, "iat": now, "exp": now + timedelta(hours=1)}
    assert client.get("/auth/me", headers=bearer(forge(good))).status_code == 200

    cases = {
        "expired": forge({**good, "exp": now - timedelta(seconds=5)}),
        "wrong key": forge(good, key="x" * 40),
        "no exp": forge({"sub": uid}),
        "no sub": forge({"exp": now + timedelta(hours=1)}),
        "not a uuid": forge({**good, "sub": "1 OR 1=1"}),
        "unknown user": forge({**good, "sub": str(uuid.uuid4())}),
        "alg none": jwt.encode(good, None, algorithm="none"),
        "other algorithm": forge(good, key=auth._secret * 2, alg="HS512"),
    }
    for name, token in cases.items():
        assert client.get("/auth/me", headers=bearer(token)).status_code == 401, name


def test_token_dies_with_the_account(database):
    body = register().json()
    with Session(database) as s:
        s.delete(s.query(models.User).one())
        s.commit()
    assert client.get("/auth/me", headers=bearer(body["access_token"])).status_code == 401


def test_throttle_memory_is_bounded(monkeypatch):
    monkeypatch.setattr(auth, "MAX_TRACKED_KEYS", 5)
    t = auth.LoginThrottle()
    for i in range(50):
        t.record_failure(f"spray{i}@x.com|1.2.3.4")
    assert len(t._fails) <= 5
    t.record_failure("victim@x.com|1.2.3.4")
    assert not t.blocked("victim@x.com|1.2.3.4") and len(t._fails) <= 5
