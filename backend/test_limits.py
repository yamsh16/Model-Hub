"""Oversized and odd input must be rejected cleanly (4xx), never reach the database as a 500.

SQLite accepts anything, so these matter most when run on PostgreSQL:
    TEST_ON_POSTGRES=1 python -m pytest test_limits.py
"""
import pytest
from fastapi.testclient import TestClient

import endpoints
from main import app

client = TestClient(app)

EP = {"name": "m", "url": "https://api.example.com/v1/chat/completions", "model": "x"}


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    monkeypatch.setattr(endpoints, "check_url_is_public", lambda url: None)
    monkeypatch.setattr(endpoints, "probe", lambda *a: 5)


@pytest.mark.parametrize(
    "field,value",
    [("name", "n" * 201), ("model", "m" * 201), ("api_key", "k" * 1001), ("name", ""), ("model", "")],
)
def test_endpoint_fields_are_bounded(field, value):
    r = client.post("/endpoints", json={**EP, field: value})
    assert r.status_code == 422, (field, r.status_code)


def test_endpoint_at_the_limits_is_accepted():
    r = client.post("/endpoints", json={**EP, "name": "n" * 200, "model": "m" * 200, "api_key": "k" * 1000})
    assert r.status_code == 201


def test_long_upload_filenames_are_shortened_not_rejected():
    r = client.post("/testsets", files={"file": ("f" * 400 + ".csv", "a,b\n1,2\n3,4\n")})
    assert r.status_code == 201 and len(r.json()["filename"]) <= 255
    r = client.post("/logs", files={"file": ("f" * 400 + ".csv", "epoch,loss\n1,1\n2,0.5\n")})
    assert r.status_code == 201 and len(r.json()["filename"]) <= 255


def test_nul_characters_are_rejected():
    # Valid UTF-8, but PostgreSQL text/JSONB cannot store U+0000.
    assert client.post("/testsets", files={"file": ("t.csv", "a,b\n1\x00,2\n3,4\n")}).status_code == 422
    assert client.post("/logs", files={"file": ("l.csv", "epoch,lo\x00ss\n1,1\n2,0.5\n")}).status_code == 422


def test_overlong_column_names_are_rejected():
    wide = "x" * 256
    assert client.post("/testsets", files={"file": ("t.csv", f"{wide},b\n1,2\n3,4\n")}).status_code == 422
    assert client.post("/logs", files={"file": ("l.csv", f"epoch,{wide}\n1,1\n2,0.5\n")}).status_code == 422


def test_metrics_input_is_bounded():
    big = ["a"] * 100_001
    assert client.post("/metrics", json={"y_true": big, "y_pred": big}).status_code == 422
