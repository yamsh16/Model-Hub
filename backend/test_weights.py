import hashlib
import os
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import config
import models
import weights
from main import app
from weights_fixtures import gguf_bytes, safetensors_bytes

client = TestClient(app)
quiet = TestClient(app, raise_server_exceptions=False)
GOOD = safetensors_bytes({"w": ("F32", [2, 2], bytes(range(16)))}, metadata={"architecture": "toy"})


def upload(data=GOOD, filename="model.safetensors", name="my model", http=client, **kw):
    return http.post(
        "/weights", params={"name": name, "filename": filename}, content=data,
        headers={"Content-Type": "application/octet-stream"}, **kw,
    )


def stored_files():
    root = config.upload_dir()
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def test_upload_scans_stores_and_describes_the_file(database):
    r = upload()
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["format"] == "safetensors" and body["name"] == "my model" and body["filename"] == "model.safetensors"
    assert body["size_bytes"] == len(GOOD) and body["sha256"] == hashlib.sha256(GOOD).hexdigest()
    assert (body["tensor_count"], body["parameter_count"], body["dtypes"]) == (1, 4, {"F32": 1})
    assert body["architecture"] == "toy"

    (path,) = stored_files()
    assert path.read_bytes() == GOOD and path.suffix == ".safetensors"
    assert path.stem == body["id"]  # named by id, never by anything the user typed
    with Session(database) as s:
        row = s.scalar(select(models.Model))
        assert row.kind == "upload" and row.file_sha256 == body["sha256"] and row.url is None


def test_gguf_upload():
    r = upload(gguf_bytes(), "tiny.gguf", "tiny")
    assert r.status_code == 201 and r.json()["format"] == "gguf" and r.json()["architecture"] == "llama"


def test_list_get_delete_round_trip():
    a = upload(name="a").json()
    b = upload(gguf_bytes(), "b.gguf", "b").json()
    assert {w["id"] for w in client.get("/weights").json()} == {a["id"], b["id"]}
    assert client.get(f"/weights/{a['id']}").json() == a
    assert len(stored_files()) == 2

    assert client.delete(f"/weights/{a['id']}").status_code == 204
    assert client.get(f"/weights/{a['id']}").status_code == 404
    assert client.delete(f"/weights/{a['id']}").status_code == 404
    assert [p.stem for p in stored_files()] == [b["id"]]  # the file went with the row


# ---- rejections leave nothing behind ----

@pytest.mark.parametrize(
    "data,filename,expect",
    [
        (GOOD, "model.pt", "Only .safetensors and .gguf"),
        (GOOD, "model.pkl", "Only .safetensors and .gguf"),
        (GOOD, "model", "Only .safetensors and .gguf"),
        (b"PK\x03\x04" + b"\0" * 64, "model.safetensors", "ZIP archive"),  # a .pt in disguise
        (b"\x80\x04\x95" + b"\0" * 64, "model.gguf", "pickle"),
        (GOOD[:-5], "model.safetensors", "truncated"),
        (gguf_bytes(), "model.safetensors", "content is GGUF"),
        (b"not a model at all", "model.gguf", "not GGUF"),
    ],
)
def test_bad_uploads_are_rejected_and_leave_no_trace(database, data, filename, expect):
    r = upload(data, filename)
    assert r.status_code == 422 and expect in r.json()["detail"], r.text
    assert stored_files() == []
    with Session(database) as s:
        assert s.scalar(select(models.Model)) is None


def test_empty_upload_rejected():
    assert upload(b"").status_code == 422


def test_missing_content_length_is_refused():
    def chunks():
        yield GOOD  # a generator body is sent chunked, without Content-Length

    r = client.post("/weights", params={"name": "n", "filename": "m.safetensors"}, content=chunks())
    assert r.status_code == 411 and stored_files() == []


def test_size_limit_refuses_before_reading_the_body(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_GB", str(100 / config.GIB))  # 100 bytes
    r = upload()
    assert r.status_code == 413 and stored_files() == []


def test_per_user_quota(monkeypatch):
    monkeypatch.setenv("MAX_USER_UPLOAD_GB", str((len(GOOD) * 1.5) / config.GIB))
    assert upload().status_code == 201
    r = upload()
    assert r.status_code == 413 and "quota" in r.json()["detail"]
    (existing,) = client.get("/weights").json()
    client.delete(f"/weights/{existing['id']}")
    assert upload().status_code == 201  # deleting frees the space


@pytest.mark.parametrize(
    "filename",
    ["../../evil.safetensors", "..\\..\\evil.safetensors", "/etc/passwd.safetensors", "a/b/c.safetensors"],
)
def test_filenames_cannot_steer_where_the_file_goes(tmp_path, filename):
    r = upload(filename=filename)
    assert r.status_code == 201
    assert "/" not in r.json()["filename"] and "\\" not in r.json()["filename"]
    root = config.upload_dir().resolve()
    assert all(root in p.resolve().parents for p in stored_files())
    assert not any(p.name == "evil.safetensors" for p in tmp_path.rglob("*"))


@pytest.mark.parametrize(
    "params",
    [{"name": "", "filename": "m.safetensors"}, {"name": "n" * 201, "filename": "m.safetensors"}, {"name": "n"}],
)
def test_query_parameters_are_validated(params):
    r = client.post("/weights", params=params, content=GOOD, headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 422


# ---- failure handling ----

def test_database_failure_after_scanning_leaves_no_file(monkeypatch):
    def boom(**kw):
        raise RuntimeError("database down")

    monkeypatch.setattr(weights, "Model", boom)
    assert upload(http=quiet).status_code == 500
    assert stored_files() == []


def test_uploaded_weights_are_not_chat_endpoints():
    wid = upload().json()["id"]
    assert client.get(f"/endpoints/{wid}").status_code == 404
    assert client.get("/endpoints").json() == []
    chat = client.post(f"/endpoints/{wid}/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert chat.status_code == 404


def test_stale_partial_uploads_are_cleaned_up():
    tmp = config.upload_dir() / "tmp"
    tmp.mkdir(parents=True)
    old, fresh = tmp / "old.part", tmp / "fresh.part"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    long_ago = time.time() - weights.STALE_TEMP_SECONDS - 60
    os.utime(old, (long_ago, long_ago))
    assert weights.cleanup_stale_temp() == 1
    assert not old.exists() and fresh.exists()
    assert weights.cleanup_stale_temp() == 0


def test_cleanup_with_no_upload_folder_is_a_no_op():
    assert weights.cleanup_stale_temp() == 0
