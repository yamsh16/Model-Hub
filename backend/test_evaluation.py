import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import endpoints
import evaluation
import testsets
from main import app

client = TestClient(app)

CSV = "word,label\nwonderful,pos\nterrible,neg\ngreat,pos\nawful,neg\n"
EP = {"name": "m", "url": "https://api.example.com/v1/chat/completions", "model": "x", "api_key": "k"}


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    testsets._testsets.clear()
    endpoints._registry.clear()
    monkeypatch.setattr(endpoints, "check_url_is_public", lambda url: None)
    monkeypatch.setattr(endpoints, "probe", lambda *a: 5)


def make(csv=CSV, target="label"):
    tid = client.post("/testsets", files={"file": ("t.csv", csv)}).json()["id"]
    if target:
        client.put(f"/testsets/{tid}/target", json={"column": target})
    eid = client.post("/endpoints", json=EP).json()["id"]
    return tid, eid


def fake_model(monkeypatch, answer):
    monkeypatch.setattr(endpoints, "chat_completion", lambda ep, prompt, **k: answer(prompt))


def test_evaluate_scores_real_replies(monkeypatch):
    # Model gets the "awful" row wrong and answers one reply verbosely.
    def answer(prompt):
        if "wonderful" in prompt:
            return "The class is: pos."
        if "awful" in prompt:
            return "pos"
        return "pos" if "great" in prompt else "neg"

    fake_model(monkeypatch, answer)
    tid, eid = make()
    r = client.post(f"/testsets/{tid}/evaluate", json={"endpoint_id": eid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n"] == 4 and body["accuracy"] == 0.75
    assert body["classes"] == ["pos", "neg"]
    assert body["confusion_matrix"] == [[2, 0], [1, 1]]
    assert body["unparsed"] == 0 and body["sampled"] is False
    assert body["roc_auc"] is None


def test_target_column_is_not_leaked_into_prompt(monkeypatch):
    seen = []
    fake_model(monkeypatch, lambda p: seen.append(p) or "pos")
    tid, eid = make()
    client.post(f"/testsets/{tid}/evaluate", json={"endpoint_id": eid})
    assert all("label:" not in p and "word:" in p for p in seen)


def test_unparseable_replies_count_as_wrong(monkeypatch):
    fake_model(monkeypatch, lambda p: "I am not sure")
    tid, eid = make()
    body = client.post(f"/testsets/{tid}/evaluate", json={"endpoint_id": eid}).json()
    assert body["unparsed"] == 4 and body["accuracy"] == 0.0
    assert body["classes"][-1] == evaluation.UNPARSED


def test_limit_samples_rows(monkeypatch):
    fake_model(monkeypatch, lambda p: "pos")
    tid, eid = make()
    body = client.post(f"/testsets/{tid}/evaluate", json={"endpoint_id": eid, "limit": 2}).json()
    assert body["n"] == 2 and body["total_rows"] == 4 and body["sampled"] is True


def test_requires_target_and_valid_ids(monkeypatch):
    tid, eid = make(target=None)
    assert client.post(f"/testsets/{tid}/evaluate", json={"endpoint_id": eid}).status_code == 422
    assert client.post("/testsets/nope/evaluate", json={"endpoint_id": eid}).status_code == 404
    client.put(f"/testsets/{tid}/target", json={"column": "label"})
    assert client.post(f"/testsets/{tid}/evaluate", json={"endpoint_id": "nope"}).status_code == 404
    assert (
        client.post(f"/testsets/{tid}/evaluate", json={"endpoint_id": eid, "limit": 9999}).status_code
        == 422
    )


def test_total_endpoint_failure_is_reported(monkeypatch):
    def boom(ep, prompt, **k):
        raise HTTPException(400, "Endpoint rejected the API key")

    monkeypatch.setattr(endpoints, "chat_completion", boom)
    tid, eid = make()
    r = client.post(f"/testsets/{tid}/evaluate", json={"endpoint_id": eid})
    assert r.status_code == 400 and "API key" in r.json()["detail"]


@pytest.mark.parametrize(
    "reply,expected",
    [("pos", "pos"), (" Neg. ", "neg"), ("**pos**", "pos"), ("it is neg", "neg"),
     ("pos or neg", None), ("positive", None), ("", None)],
)
def test_parse_label(reply, expected):
    assert evaluation.parse_label(reply, ["pos", "neg"]) == expected


def test_chat_completion_parses_openai_reply(monkeypatch):
    class R:
        status_code, is_success = 200, True

        def json(self):
            return {"choices": [{"message": {"content": "pos"}}]}

    monkeypatch.undo()
    monkeypatch.setattr(endpoints.httpx, "post", lambda *a, **k: R())
    assert endpoints.chat_completion({"url": "https://x", "model": "m", "api_key": None}, "hi") == "pos"

    class Bad(R):
        def json(self):
            return {"nope": 1}

    monkeypatch.setattr(endpoints.httpx, "post", lambda *a, **k: Bad())
    with pytest.raises(HTTPException) as e:
        endpoints.chat_completion({"url": "https://x", "model": "m", "api_key": None}, "hi")
    assert e.value.status_code == 502
