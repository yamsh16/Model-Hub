import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import endpoints
from main import app

client = TestClient(app)

PAYLOAD = {
    "name": "my-llm",
    "url": "https://api.example.com/v1/chat/completions",
    "model": "gpt-x",
    "api_key": "sk-secret",
}


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    endpoints._registry.clear()
    monkeypatch.setattr(endpoints, "check_url_is_public", lambda url: None)
    monkeypatch.setattr(endpoints, "probe", lambda url, model, key: 42)


def test_register_and_read_back_without_key():
    r = client.post("/endpoints", json=PAYLOAD)
    assert r.status_code == 201
    body = r.json()
    assert body["latency_ms"] == 42
    assert "api_key" not in body
    assert "sk-secret" not in r.text

    got = client.get(f"/endpoints/{body['id']}")
    assert got.status_code == 200 and got.json() == body
    assert client.get("/endpoints").json() == [body]


def test_delete_then_404():
    eid = client.post("/endpoints", json=PAYLOAD).json()["id"]
    assert client.delete(f"/endpoints/{eid}").status_code == 204
    assert client.get(f"/endpoints/{eid}").status_code == 404
    assert client.delete(f"/endpoints/{eid}").status_code == 404


def test_invalid_url_rejected():
    r = client.post("/endpoints", json={**PAYLOAD, "url": "not a url"})
    assert r.status_code == 422


def test_failed_probe_is_not_registered(monkeypatch):
    def bad_probe(url, model, key):
        raise HTTPException(400, "Endpoint rejected the API key")

    monkeypatch.setattr(endpoints, "probe", bad_probe)
    r = client.post("/endpoints", json=PAYLOAD)
    assert r.status_code == 400
    assert client.get("/endpoints").json() == []


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/v1", "http://localhost/v1", "http://169.254.169.254/latest"]
)
def test_ssrf_guard_blocks_local_addresses(url, monkeypatch):
    monkeypatch.undo()  # use the real guard
    monkeypatch.delenv("ALLOW_PRIVATE_ENDPOINTS", raising=False)
    with pytest.raises(HTTPException) as e:
        endpoints.check_url_is_public(url)
    assert e.value.status_code == 422


def test_private_allowed_with_env_flag(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setenv("ALLOW_PRIVATE_ENDPOINTS", "1")
    endpoints.check_url_is_public("http://127.0.0.1:11434/v1/chat/completions")


def test_probe_maps_upstream_errors(monkeypatch):
    monkeypatch.undo()

    class R:
        def __init__(self, code):
            self.status_code = code
            self.is_success = 200 <= code < 300

    for code, expected in [(401, 400), (500, 400)]:
        monkeypatch.setattr(endpoints.httpx, "post", lambda *a, _c=code, **k: R(_c))
        with pytest.raises(HTTPException) as e:
            endpoints.probe("https://x", "m", None)
        assert e.value.status_code == expected

    monkeypatch.setattr(endpoints.httpx, "post", lambda *a, **k: R(200))
    assert endpoints.probe("https://x", "m", "k") >= 0
