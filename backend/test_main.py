from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "model-hub-api"


def test_cors_allows_frontend_origin():
    r = client.get("/health", headers={"Origin": "http://localhost:8000"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:8000"
