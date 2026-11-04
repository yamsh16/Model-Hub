"""Starting, stopping and chatting with uploaded GGUF models (the sandbox itself is faked here)."""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import config
import sandbox
import serving
from main import app
from weights_fixtures import gguf_bytes, safetensors_bytes

client = TestClient(app)
GGUF = gguf_bytes()


class FakeSandbox:
    def __init__(self):
        self.started, self.stopped, self.payloads = [], [], []
        self.alive = True
        self.fail_start = None
        self.reply = "hello from the model"
        self.stream_words = ["Hel", "lo"]
        self.chat_error = None

    def start(self, name, path, limits, template, labels):
        if self.fail_start:
            raise self.fail_start
        self.started.append({"name": name, "path": path, "template": template, "labels": labels})

    def stop(self, name):
        self.stopped.append(name)

    def is_running(self, name):
        return self.alive

    def chat_once(self, name, payload):
        self.payloads.append(payload)
        if self.chat_error:
            raise self.chat_error
        return self.reply

    def chat_stream(self, name, payload):
        self.payloads.append(payload)
        for w in self.stream_words:
            yield "data: " + json.dumps({"choices": [{"delta": {"content": w}}]})
        yield "data: [DONE]"

    def stop_all_managed(self):
        return 3


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    f = FakeSandbox()
    for name in ("start", "stop", "is_running", "chat_once", "chat_stream", "stop_all_managed"):
        monkeypatch.setattr(sandbox, name, getattr(f, name))
    serving.manager.__init__()  # a clean registry for every test
    yield f
    serving.manager.__init__()


def upload(data=GGUF, filename="m.gguf", name="m", headers=None):
    r = client.post(
        "/weights", params={"name": name, "filename": filename}, content=data,
        headers={"Content-Type": "application/octet-stream", **(headers or {})},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


CHAT = {"messages": [{"role": "user", "content": "hi"}]}


# ------------------------------------------------------------------------------- lifecycle

def test_start_chat_stop(fake):
    wid = upload()
    assert client.get(f"/weights/{wid}/serve").json()["state"] == "stopped"

    r = client.post(f"/weights/{wid}/serve")
    assert r.status_code == 200 and r.json()["state"] == "running" and r.json()["chat_template"] == "chatml"
    (started,) = fake.started
    assert started["name"] == f"modelhub-{wid}" and started["path"].endswith(f"{wid}.gguf")
    assert started["labels"]["modelhub.model"] == wid

    chat = client.post(f"/weights/{wid}/chat", json=CHAT)
    assert chat.status_code == 200 and chat.json() == {"reply": "hello from the model"}

    assert client.delete(f"/weights/{wid}/serve").status_code == 204
    assert fake.stopped == [f"modelhub-{wid}"]
    assert client.get(f"/weights/{wid}/serve").json()["state"] == "stopped"
    assert client.delete(f"/weights/{wid}/serve").status_code == 204  # stopping twice is fine
    assert client.post(f"/weights/{wid}/chat", json=CHAT).status_code == 409


def test_starting_twice_does_not_start_a_second_container(fake):
    wid = upload()
    client.post(f"/weights/{wid}/serve")
    assert client.post(f"/weights/{wid}/serve").json()["state"] == "running"
    assert len(fake.started) == 1


def test_chat_requires_the_model_to_be_started(fake):
    wid = upload()
    r = client.post(f"/weights/{wid}/chat", json=CHAT)
    assert r.status_code == 409 and "Start the model first" in r.json()["detail"]
    assert fake.payloads == []


def test_chat_template_is_chosen_from_the_allow_list(fake):
    wid = upload()
    bad = client.post(f"/weights/{wid}/serve", json={"chat_template": "{{ config }}"})
    assert bad.status_code == 422 and fake.started == []
    ok = client.post(f"/weights/{wid}/serve", json={"chat_template": "llama3"})
    assert ok.json()["chat_template"] == "llama3" and fake.started[0]["template"] == "llama3"


def test_safetensors_are_stored_but_not_served(fake):
    wid = upload(safetensors_bytes(), "m.safetensors")
    r = client.post(f"/weights/{wid}/serve")
    assert r.status_code == 422 and "GGUF" in r.json()["detail"] and fake.started == []


def test_a_model_too_big_for_the_memory_limit_is_refused(fake, monkeypatch):
    wid = upload()
    monkeypatch.setenv("SERVE_MEMORY", str(len(GGUF) + 10))  # file is > 80% of the limit
    r = client.post(f"/weights/{wid}/serve")
    assert r.status_code == 422 and "too large" in r.json()["detail"] and fake.started == []


def test_a_missing_file_is_reported(fake):
    wid = upload()
    for p in config.upload_dir().rglob("*.gguf"):
        p.unlink()
    r = client.post(f"/weights/{wid}/serve")
    assert r.status_code == 409 and "missing" in r.json()["detail"]


def test_deleting_a_model_stops_its_container_first(fake):
    wid = upload()
    client.post(f"/weights/{wid}/serve")
    assert client.delete(f"/weights/{wid}").status_code == 204
    assert fake.stopped == [f"modelhub-{wid}"]
    assert serving.manager.all() == []


# ------------------------------------------------------------------------------ failure paths

def test_docker_not_running_is_a_503_and_leaves_no_state(fake):
    wid = upload()
    fake.fail_start = sandbox.SandboxUnavailable("Docker is not running on the server")
    r = client.post(f"/weights/{wid}/serve")
    assert r.status_code == 503 and "Docker is not running" in r.json()["detail"]
    assert serving.manager.all() == []
    fake.fail_start = None
    assert client.post(f"/weights/{wid}/serve").status_code == 200  # can simply try again


def test_a_model_that_fails_to_load_is_a_502_with_the_reason(fake):
    wid = upload()
    fake.fail_start = sandbox.SandboxError("The model failed to load: bad tensor")
    r = client.post(f"/weights/{wid}/serve")
    assert r.status_code == 502 and "bad tensor" in r.json()["detail"]
    assert serving.manager.all() == []  # the slot was released


def test_an_unexpected_crash_while_starting_still_releases_the_slot(fake):
    wid = upload()
    fake.fail_start = RuntimeError("boom")
    assert TestClient(app, raise_server_exceptions=False).post(f"/weights/{wid}/serve").status_code == 500
    assert serving.manager.all() == []


def test_a_dead_container_is_detected_and_forgotten(fake):
    wid = upload()
    client.post(f"/weights/{wid}/serve")
    fake.alive = False  # e.g. killed for exceeding its memory limit
    r = client.post(f"/weights/{wid}/chat", json=CHAT)
    assert r.status_code == 409 and "stopped" in r.json()["detail"]
    assert client.get(f"/weights/{wid}/serve").json()["state"] == "stopped"


def test_model_errors_reach_the_user(fake):
    wid = upload()
    client.post(f"/weights/{wid}/serve")
    fake.chat_error = sandbox.SandboxError("The model took too long to answer")
    r = client.post(f"/weights/{wid}/chat", json=CHAT)
    assert r.status_code == 502 and "too long" in r.json()["detail"]


# ---------------------------------------------------------------------------------- limits

def test_one_running_model_per_user_by_default(fake):
    a, b = upload(name="a"), upload(name="b")
    assert client.post(f"/weights/{a}/serve").status_code == 200
    r = client.post(f"/weights/{b}/serve")
    assert r.status_code == 409 and "already have a model running" in r.json()["detail"]
    client.delete(f"/weights/{a}/serve")
    assert client.post(f"/weights/{b}/serve").status_code == 200  # freed by stopping


def test_a_global_cap_protects_the_server(fake, monkeypatch):
    monkeypatch.setenv("SERVE_MAX_RUNNING", "1")
    monkeypatch.setenv("SERVE_MAX_PER_USER", "5")
    a, b = upload(name="a"), upload(name="b")
    assert client.post(f"/weights/{a}/serve").status_code == 200
    r = client.post(f"/weights/{b}/serve")
    assert r.status_code == 503 and "try again later" in r.json()["detail"]


def test_callers_cannot_raise_the_generation_limit(fake, monkeypatch):
    monkeypatch.setenv("SERVE_MAX_TOKENS", "64")
    wid = upload()
    client.post(f"/weights/{wid}/serve")
    client.post(f"/weights/{wid}/chat", json={**CHAT, "max_tokens": 999999, "temperature": 99})
    assert fake.payloads[0]["max_tokens"] == 64 and "temperature" not in fake.payloads[0]


def test_a_busy_model_answers_429_and_frees_its_slots(fake):
    wid = upload()
    client.post(f"/weights/{wid}/serve")
    entry = serving.manager.get(__import__("uuid").UUID(wid))
    taken = [entry.slots.acquire(blocking=False) for _ in range(serving.MAX_PARALLEL_CHATS_PER_MODEL)]
    assert all(taken)
    assert client.post(f"/weights/{wid}/chat", json=CHAT).status_code == 429
    for _ in taken:
        entry.slots.release()
    assert client.post(f"/weights/{wid}/chat", json=CHAT).status_code == 200


def test_slots_are_released_after_errors_and_after_streams(fake):
    wid = upload()
    client.post(f"/weights/{wid}/serve")

    fake.chat_error = sandbox.SandboxError("nope")
    for _ in range(4):
        client.post(f"/weights/{wid}/chat", json=CHAT)  # four failures in a row...
    fake.chat_error = None
    for _ in range(4):
        client.post(f"/weights/{wid}/chat", json={**CHAT, "stream": True})  # ...and four streams
    assert client.post(f"/weights/{wid}/chat", json=CHAT).status_code == 200  # no slot leaked


# ----------------------------------------------------------------------------------- streaming

def test_streamed_chat_uses_the_same_event_format_as_endpoint_chat(fake):
    wid = upload()
    client.post(f"/weights/{wid}/serve")
    r = client.post(f"/weights/{wid}/chat", json={**CHAT, "stream": True})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [e[len("data: "):] for e in r.text.strip().split("\n\n")]
    assert [json.loads(e)["delta"] for e in events[:-1]] == ["Hel", "lo"] and events[-1] == "[DONE]"
    assert fake.payloads[0]["stream"] is True


def test_a_failure_mid_stream_becomes_an_error_event(fake, monkeypatch):
    wid = upload()
    client.post(f"/weights/{wid}/serve")

    def broken(name, payload):
        yield "data: " + json.dumps({"choices": [{"delta": {"content": "partial"}}]})
        raise sandbox.SandboxError("The model stopped")

    monkeypatch.setattr(sandbox, "chat_stream", broken)
    r = client.post(f"/weights/{wid}/chat", json={**CHAT, "stream": True})
    events = [json.loads(e[len("data: "):]) for e in r.text.strip().split("\n\n")]
    assert events[0] == {"delta": "partial"} and "stopped" in events[1]["error"]


# -------------------------------------------------------------------- idle sweep and restarts

def test_idle_models_are_stopped_and_active_ones_are_not(fake):
    a = upload(name="a")
    client.post(f"/weights/{a}/serve")
    entry = serving.manager.get(__import__("uuid").UUID(a))
    entry.last_used = time.monotonic() - config.serve_idle_seconds() - 1
    assert serving.reap_idle() == 1 and fake.stopped == [f"modelhub-{a}"]
    assert client.get(f"/weights/{a}/serve").json()["state"] == "stopped"

    client.post(f"/weights/{a}/serve")
    assert serving.reap_idle() == 0  # just used: stays up


def test_chatting_keeps_a_model_alive(fake):
    wid = upload()
    client.post(f"/weights/{wid}/serve")
    entry = serving.manager.get(__import__("uuid").UUID(wid))
    entry.last_used = time.monotonic() - config.serve_idle_seconds() + 5  # about to expire
    client.post(f"/weights/{wid}/chat", json=CHAT)
    assert entry.last_used > time.monotonic() - 5


def test_startup_removes_leftover_containers_and_shutdown_stops_everything(fake, monkeypatch):
    called = []
    monkeypatch.setattr(sandbox, "stop_all_managed", lambda: called.append(1) or 2)
    serving.startup_cleanup()
    assert called == [1]
    monkeypatch.setattr(sandbox, "stop_all_managed", lambda: (_ for _ in ()).throw(sandbox.SandboxUnavailable("no docker")))
    serving.startup_cleanup()  # no Docker is not an error at startup

    wid = upload()
    client.post(f"/weights/{wid}/serve")
    serving.shutdown_all()
    assert fake.stopped == [f"modelhub-{wid}"] and serving.manager.all() == []


def test_concurrent_starts_of_one_model_start_one_container(fake):
    wid = upload()
    gate = threading.Event()
    original = fake.start

    def slow_start(*a, **kw):
        gate.wait(2)
        return original(*a, **kw)

    sandbox.start = slow_start
    results = []
    t = threading.Thread(target=lambda: results.append(client.post(f"/weights/{wid}/serve").status_code))
    t.start()
    time.sleep(0.2)  # first request is now "starting"
    second = client.post(f"/weights/{wid}/serve")
    assert second.status_code == 409 and "still starting" in second.json()["detail"]
    assert client.post(f"/weights/{wid}/chat", json=CHAT).status_code == 409
    gate.set()
    t.join()
    assert results == [200] and len(fake.started) == 1


def test_the_model_list_says_which_models_are_running(fake):
    g = upload(name="g")
    upload(safetensors_bytes(), "s.safetensors", "s")

    def states():
        return {w["name"]: w["serve_state"] for w in client.get("/weights").json()}

    assert states() == {"g": "stopped", "s": "unsupported"}
    client.post(f"/weights/{g}/serve")
    assert states() == {"g": "running", "s": "unsupported"}
    client.delete(f"/weights/{g}/serve")
    assert states()["g"] == "stopped"
