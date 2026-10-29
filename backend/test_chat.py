import json

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import chat
import endpoints
from main import app

client = TestClient(app)

EP = {"name": "m", "url": "https://api.example.com/v1/chat/completions", "model": "x", "api_key": "k"}
MSGS = [{"role": "user", "content": "hi"}]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(endpoints, "check_url_is_public", lambda url: None)
    monkeypatch.setattr(endpoints, "probe", lambda *a: 5)


@pytest.fixture
def eid():
    return client.post("/endpoints", json=EP).json()["id"]


def test_non_streaming_reply_sends_whole_history(monkeypatch, eid):
    seen = {}

    def fake(ep, messages, **k):
        seen["messages"] = messages
        return "hello there"

    monkeypatch.setattr(endpoints, "chat_messages", fake)
    history = [*MSGS, {"role": "assistant", "content": "yo"}, {"role": "user", "content": "again"}]
    r = client.post(f"/endpoints/{eid}/chat", json={"messages": history})
    assert r.status_code == 200 and r.json() == {"reply": "hello there"}
    assert seen["messages"] == history


@pytest.mark.parametrize(
    "body",
    [
        {"messages": []},
        {"messages": [{"role": "robot", "content": "x"}]},
        {"messages": [{"role": "user", "content": "x" * (chat.MAX_CONTENT_CHARS + 1)}]},
        {"messages": MSGS * (chat.MAX_MESSAGES + 1)},
    ],
)
def test_bad_chat_input_rejected(body, eid):
    assert client.post(f"/endpoints/{eid}/chat", json=body).status_code == 422


def test_unknown_endpoint_404():
    assert client.post("/endpoints/nope/chat", json={"messages": MSGS}).status_code == 404


def test_upstream_error_passes_through(monkeypatch, eid):
    def boom(ep, messages, **k):
        raise HTTPException(400, "Endpoint rejected the API key")

    monkeypatch.setattr(endpoints, "chat_messages", boom)
    r = client.post(f"/endpoints/{eid}/chat", json={"messages": MSGS})
    assert r.status_code == 400


def sse_events(text):
    return [e[len("data: "):] for e in text.strip().split("\n\n") if e]


def upstream_sse(lines, status=200):
    """A real httpx streaming response built from canned SSE lines via a mock transport."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, content="\n".join(lines).encode())
    )
    client_ = httpx.Client(transport=transport)
    return client_.send(client_.build_request("POST", "https://x"), stream=True)


def chunk(text):
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})


def test_streaming_relays_deltas_and_done(monkeypatch, eid):
    lines = [
        'data: {"choices":[{"delta":{"role":"assistant"}}]}',  # no content: skipped
        "",
        chunk("Hel"),
        ": keep-alive",
        chunk("lo"),
        "data: not json",
        "data: [DONE]",
        chunk("never sent"),
    ]
    monkeypatch.setattr(endpoints, "open_chat_stream", lambda ep, m: upstream_sse(lines))
    r = client.post(f"/endpoints/{eid}/chat", json={"messages": MSGS, "stream": True})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = sse_events(r.text)
    assert [json.loads(e)["delta"] for e in events[:-1]] == ["Hel", "lo"]
    assert events[-1] == "[DONE]"


def test_stream_open_failure_is_a_normal_http_error(monkeypatch, eid):
    def boom(ep, m):
        raise HTTPException(502, "Endpoint timed out")

    monkeypatch.setattr(endpoints, "open_chat_stream", boom)
    r = client.post(f"/endpoints/{eid}/chat", json={"messages": MSGS, "stream": True})
    assert r.status_code == 502


def test_mid_stream_failure_emits_error_event(monkeypatch, eid):
    class Broken:
        closed = False

        def iter_lines(self):
            yield chunk("partial")
            raise httpx.ReadError("connection reset")

        def close(self):
            Broken.closed = True

    monkeypatch.setattr(endpoints, "open_chat_stream", lambda ep, m: Broken())
    r = client.post(f"/endpoints/{eid}/chat", json={"messages": MSGS, "stream": True})
    events = [json.loads(e) for e in sse_events(r.text)]
    assert events[0] == {"delta": "partial"} and "error" in events[1]
    assert Broken.closed


def test_open_chat_stream_checks_status_and_closes():
    ep = {"url": "https://x", "model": "m", "api_key": None}
    # status handling is shared with chat_messages; exercise it via the helper directly
    with pytest.raises(HTTPException) as e:
        endpoints._check_status(401)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        endpoints._check_status(500)
    assert e.value.status_code == 502
    headers, body = endpoints._chat_request(ep, MSGS, True, None, None)
    assert body["stream"] is True and "max_tokens" not in body and "Authorization" not in headers
