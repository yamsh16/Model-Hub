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
    ["http://localhost:3000", "http://127.0.0.1:3000"],
)
def test_cors_allows_frontend_origins(origin):
    r = client.get("/health", headers={"Origin": origin})
    assert r.headers["access-control-allow-origin"] == origin


def test_cors_rejects_other_origins():
    r = client.get("/health", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_origins_come_from_the_environment(monkeypatch):
    import config

    monkeypatch.setenv("CORS_ORIGINS", " https://app.example.com/ ,http://localhost:3100,")
    assert config.cors_origins() == ["https://app.example.com", "http://localhost:3100"]  # trimmed, no trailing slash
    monkeypatch.delenv("CORS_ORIGINS")
    assert config.cors_origins() == ["http://localhost:3000", "http://127.0.0.1:3000"]
    monkeypatch.setenv("CORS_ORIGINS", "")
    assert config.cors_origins() == ["http://localhost:3000", "http://127.0.0.1:3000"]  # empty means default


def test_generated_secrets_follow_secrets_dir_and_persist(monkeypatch, tmp_path):
    import config

    monkeypatch.delenv("DOES_NOT_EXIST_SECRET", raising=False)
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path / "new" / "dir"))  # created if missing
    first = config.get_secret("DOES_NOT_EXIST_SECRET", lambda: "generated-once")
    assert first == "generated-once"
    assert (tmp_path / "new" / "dir" / "DOES_NOT_EXIST_SECRET").read_text() == "generated-once"
    assert config.get_secret("DOES_NOT_EXIST_SECRET", lambda: "different") == "generated-once"  # reused, not regenerated
    monkeypatch.setenv("DOES_NOT_EXIST_SECRET", "from-env")
    assert config.get_secret("DOES_NOT_EXIST_SECRET", lambda: "x") == "from-env"  # the environment wins
