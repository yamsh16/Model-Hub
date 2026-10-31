import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "model-hub-api"


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:3000", "http://127.0.0.1:3000"],
)
def test_cors_allows_frontend_origins(origin):
    r = client.get("/health", headers={"Origin": origin})
    assert r.headers["access-control-allow-origin"] == origin


def test_cors_rejects_other_origins():
    r = client.get("/health", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in r.headers
