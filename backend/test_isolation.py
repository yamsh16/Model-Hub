"""Per-user isolation: one user must never see, change, use or learn about another's data.

These tests use real sign-ins (two users, real tokens), not the shortcut in conftest.
"""
import uuid

import jwt
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import auth
import endpoints
import models
from main import app

client = TestClient(app)

EP = {"name": "m", "url": "https://api.example.com/v1/chat/completions", "model": "x", "api_key": "sk-alice-secret"}
CSV = "word,label\n" + "".join(f"w{i},{'pos' if i % 2 else 'neg'}\n" for i in range(10))
LOG = "epoch,loss,val_loss\n" + "".join(f"{i},{1 / (i + 1):.4f},{1.2 / (i + 1):.4f}\n" for i in range(12))
PASSWORD = "a perfectly fine password"


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    monkeypatch.setattr(endpoints, "check_url_is_public", lambda url: None)
    monkeypatch.setattr(endpoints, "probe", lambda *a: 5)
    monkeypatch.setattr(endpoints, "chat_messages", lambda ep, messages, **k: "hello")
    monkeypatch.setattr(endpoints, "chat_completion", lambda ep, prompt, **k: "pos")


def sign_up(email):
    r = client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def alice():
    return sign_up("alice@example.com")


@pytest.fixture
def bob():
    return sign_up("bob@example.com")


@pytest.fixture
def alices(alice):
    """Everything Alice owns: an endpoint, an evaluated test set with leakage info, and a log."""
    eid = client.post("/endpoints", json=EP, headers=alice).json()["id"]
    tid = client.post("/testsets", files={"file": ("t.csv", CSV)}, headers=alice).json()["id"]
    assert client.put(f"/testsets/{tid}/target", json={"column": "label"}, headers=alice).status_code == 200
    assert client.post(f"/testsets/{tid}/evaluate", json={"endpoint_id": eid}, headers=alice).status_code == 200
    client.post(f"/testsets/{tid}/leakage", files={"train_file": ("tr.csv", CSV)}, headers=alice)
    lid = client.post("/logs", files={"file": ("l.csv", LOG)}, headers=alice).json()["id"]
    return {"endpoint": eid, "testset": tid, "log": lid}


# ---------- nothing works without signing in ----------

OPEN_ROUTES = {("GET", "/health"), ("POST", "/auth/register"), ("POST", "/auth/login")}


def test_every_route_requires_sign_in_except_the_public_ones():
    """Walks the real route table, so a route added later can't quietly skip authentication."""
    checked = 0
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue  # docs/openapi pages
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if (method, route.path) in OPEN_ROUTES:
                continue
            path = route.path.replace("{endpoint_id}", "x").replace("{testset_id}", "x").replace("{log_id}", "x")
            r = client.request(method, path)
            assert r.status_code == 401, f"{method} {route.path} answered {r.status_code} without a token"
            checked += 1
    assert checked >= 15  # guards against the loop silently checking nothing


# ---------- the other user's ids behave exactly like ids that don't exist ----------

def test_bob_cannot_see_or_touch_alices_endpoint(alice, bob, alices):
    eid = alices["endpoint"]
    assert client.get("/endpoints", headers=bob).json() == []
    assert client.get(f"/endpoints/{eid}", headers=bob).status_code == 404
    assert client.delete(f"/endpoints/{eid}", headers=bob).status_code == 404
    chat = client.post(f"/endpoints/{eid}/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=bob)
    assert chat.status_code == 404
    # ...and Alice's endpoint survived Bob's delete attempt
    assert client.get(f"/endpoints/{eid}", headers=alice).status_code == 200
    assert [e["id"] for e in client.get("/endpoints", headers=alice).json()] == [eid]


def test_bob_cannot_see_or_touch_alices_testset(alice, bob, alices):
    tid = alices["testset"]
    assert client.get(f"/testsets/{tid}", headers=bob).status_code == 404
    assert client.put(f"/testsets/{tid}/target", json={"column": "word"}, headers=bob).status_code == 404
    leak = client.post(f"/testsets/{tid}/leakage", files={"train_file": ("t.csv", CSV)}, headers=bob)
    assert leak.status_code == 404
    # Alice's target was not changed by Bob's attempt
    assert client.get(f"/testsets/{tid}", headers=alice).json()["target"] == "label"


def test_bob_cannot_use_alices_endpoint_or_test_set_in_an_evaluation(alice, bob, alices):
    bob_eid = client.post("/endpoints", json={**EP, "api_key": "sk-bob"}, headers=bob).json()["id"]
    bob_tid = client.post("/testsets", files={"file": ("t.csv", CSV)}, headers=bob).json()["id"]
    client.put(f"/testsets/{bob_tid}/target", json={"column": "label"}, headers=bob)

    on_alices_set = client.post(f"/testsets/{alices['testset']}/evaluate", json={"endpoint_id": bob_eid}, headers=bob)
    with_alices_endpoint = client.post(f"/testsets/{bob_tid}/evaluate", json={"endpoint_id": alices["endpoint"]}, headers=bob)
    own = client.post(f"/testsets/{bob_tid}/evaluate", json={"endpoint_id": bob_eid}, headers=bob)
    assert (on_alices_set.status_code, with_alices_endpoint.status_code, own.status_code) == (404, 404, 200)


def test_bob_cannot_see_alices_logs_or_estimates(alice, bob, alices):
    lid = alices["log"]
    assert client.get(f"/logs/{lid}", headers=bob).status_code == 404
    assert client.get(f"/logs/{lid}/estimate", headers=bob).status_code == 404
    assert client.get(f"/logs/{lid}", headers=alice).status_code == 200


def test_readiness_only_reads_your_own_data(alice, bob, alices):
    mine = client.get("/readiness", params={"testset_id": alices["testset"], "log_id": alices["log"]}, headers=alice)
    assert mine.status_code == 200 and mine.json()["score"] is not None
    for params in ({"testset_id": alices["testset"]}, {"log_id": alices["log"]},
                   {"testset_id": alices["testset"], "log_id": alices["log"]}):
        assert client.get("/readiness", params=params, headers=bob).status_code == 404


def test_unknown_and_malformed_ids_give_the_same_answer(alice, alices):
    for bad in (str(uuid.uuid4()), "not-a-uuid", "1 OR 1=1", "..%2F..%2Fetc"):
        for path in (f"/endpoints/{bad}", f"/testsets/{bad}", f"/logs/{bad}", f"/logs/{bad}/estimate"):
            r = client.get(path, headers=alice)
            assert r.status_code == 404, (path, r.status_code)
    assert client.get("/readiness", params={"testset_id": "nope"}, headers=alice).status_code == 404


def test_404_does_not_reveal_whether_the_id_exists(alice, bob, alices):
    """Bob asking about Alice's real id and about a made-up id gets the same answer."""
    real = client.get(f"/endpoints/{alices['endpoint']}", headers=bob)
    fake = client.get(f"/endpoints/{uuid.uuid4()}", headers=bob)
    assert real.status_code == fake.status_code == 404 and real.json() == fake.json()


# ---------- secrets ----------

def test_api_key_is_encrypted_at_rest_and_never_returned(alice, alices, database):
    with Session(database) as s:
        stored = s.scalar(select(models.Model)).api_key_encrypted
    assert stored and "sk-alice-secret" not in stored
    for path in (f"/endpoints/{alices['endpoint']}", "/endpoints"):
        text = client.get(path, headers=alice).text
        assert "sk-alice-secret" not in text and stored not in text
    # ...yet the server can still use it: that is what chat and evaluate do.
    with Session(database) as s:
        user = s.scalar(select(models.User))
        assert endpoints.get_endpoint(s, user, alices["endpoint"])["api_key"] == "sk-alice-secret"


# ---------- accounts ----------

def test_deleting_an_account_removes_its_data_and_voids_its_token(alice, bob, alices, database):
    bob_eid = client.post("/endpoints", json=EP, headers=bob).json()["id"]
    with Session(database) as s:
        s.delete(s.scalar(select(models.User).where(models.User.email == "alice@example.com")))
        s.commit()
    assert client.get("/endpoints", headers=alice).status_code == 401
    with Session(database) as s:
        bob_id = s.scalar(select(models.User.id).where(models.User.email == "bob@example.com"))
        for table in (models.Model, models.TestSet, models.TrainingLog):
            assert {row.user_id for row in s.scalars(select(table))} <= {bob_id}  # only Bob's rows remain
    assert client.get(f"/endpoints/{bob_eid}", headers=bob).status_code == 200


def test_a_token_cannot_be_edited_to_become_another_user(alice, bob):
    bob_id = client.get("/auth/me", headers=bob).json()["id"]
    claims = jwt.decode(alice["Authorization"].split()[1], options={"verify_signature": False})
    forged = jwt.encode({**claims, "sub": bob_id}, "guessed-key-" * 4, algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    # Control: the same claims signed with the real key are accepted, so the 401 above is
    # about the signature and not some other defect in the forged token.
    genuine = jwt.encode({**claims, "sub": bob_id}, auth._secret, algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {genuine}"}).json()["email"] == "bob@example.com"
