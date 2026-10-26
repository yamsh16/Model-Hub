import numpy as np
import pytest
from fastapi.testclient import TestClient

import endpoints
import logs
import testsets
from main import app

client = TestClient(app)

EP = {"name": "m", "url": "https://api.example.com/v1/chat/completions", "model": "x", "api_key": "k"}


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    for store in (testsets._testsets, endpoints._registry, logs._logs):
        store.clear()
    monkeypatch.setattr(endpoints, "check_url_is_public", lambda url: None)
    monkeypatch.setattr(endpoints, "probe", lambda *a: 5)


def make_testset(rows, answer, with_target=True):
    """rows: list of (word, label). `answer(word)` is what the fake model replies."""
    csv = "word,label\n" + "".join(f"{w},{l}\n" for w, l in rows)
    tid = client.post("/testsets", files={"file": ("t.csv", csv)}).json()["id"]
    if with_target:
        client.put(f"/testsets/{tid}/target", json={"column": "label"})
    return tid


def evaluate(tid, monkeypatch, answer):
    eid = client.post("/endpoints", json=EP).json()["id"]
    monkeypatch.setattr(
        endpoints, "chat_completion",
        lambda ep, prompt, **k: answer(prompt.split("word: ")[1].split("\n")[0].strip()),
    )
    assert client.post(f"/testsets/{tid}/evaluate", json={"endpoint_id": eid}).status_code == 200


def log_id(rows):
    csv = "epoch,loss,val_loss\n" + "".join(f"{i},{t},{v}\n" for i, (t, v) in enumerate(rows))
    return client.post("/logs", files={"file": ("l.csv", csv)}).json()["id"]


def get(**params):
    r = client.get("/readiness", params=params)
    assert r.status_code == 200, r.text
    return r.json()


ROWS = [(f"w{i}", "pos" if i % 2 == 0 else "neg") for i in range(40)]
PERFECT = lambda w: "pos" if int(w[1:]) % 2 == 0 else "neg"  # noqa: E731


def test_requires_an_id_and_known_ids():
    assert client.get("/readiness").status_code == 422
    assert client.get("/readiness", params={"testset_id": "nope"}).status_code == 404
    assert client.get("/readiness", params={"log_id": "nope"}).status_code == 404


def test_no_evaluation_means_no_score_and_says_what_to_do(monkeypatch):
    tid = make_testset(ROWS, PERFECT)
    body = get(testset_id=tid)
    assert body["score"] is None and "Run an evaluation" in body["verdict"]
    assert "Evaluate" in body["advice"][0]["text"]


def test_perfect_model_scores_high(monkeypatch):
    tid = make_testset(ROWS, PERFECT)
    evaluate(tid, monkeypatch, PERFECT)
    body = get(testset_id=tid)
    assert body["score"] >= 90
    assert {c["name"] for c in body["components"]} == {"quality", "weakest_class"}
    assert body["coverage"] == pytest.approx(0.55)
    assert "based on 2 of 5 signals" in body["verdict"]  # partial evidence is stated


def test_always_wrong_model_scores_low_and_gets_advice(monkeypatch):
    tid = make_testset(ROWS, PERFECT)
    evaluate(tid, monkeypatch, lambda w: "neg" if PERFECT(w) == "pos" else "pos")
    body = get(testset_id=tid)
    assert body["score"] <= 10
    texts = " ".join(a["text"] for a in body["advice"])
    assert "Weakest on" in texts and "Most often mistakes" in texts


def test_unparseable_replies_flagged_and_do_not_depress_macro_f1(monkeypatch):
    tid = make_testset(ROWS, PERFECT)
    evaluate(tid, monkeypatch, lambda w: PERFECT(w) if int(w[1:]) % 10 else "no idea")
    body = get(testset_id=tid)
    assert any("didn't name a single class" in a["text"] for a in body["advice"])


def test_majority_baseline_warning(monkeypatch):
    rows = [(f"w{i}", "pos" if i < 36 else "neg") for i in range(40)]
    tid = make_testset(rows, None)
    evaluate(tid, monkeypatch, lambda w: "pos")
    texts = " ".join(a["text"] for a in get(testset_id=tid)["advice"])
    assert "barely above always guessing" in texts


def test_leakage_is_top_priority_and_lowers_score(monkeypatch):
    tid = make_testset(ROWS, PERFECT)
    evaluate(tid, monkeypatch, PERFECT)
    clean = get(testset_id=tid)["score"]
    train = "word,label\n" + "".join(f"{w},{l}\n" for w, l in ROWS[:10])
    client.post(f"/testsets/{tid}/leakage", files={"train_file": ("tr.csv", train)})
    body = get(testset_id=tid)
    assert body["score"] < clean
    assert body["advice"][0]["priority"] == 1 and "inflated" in body["advice"][0]["text"]


def test_changing_target_discards_stale_evaluation(monkeypatch):
    tid = make_testset(ROWS, PERFECT)
    evaluate(tid, monkeypatch, PERFECT)
    client.put(f"/testsets/{tid}/target", json={"column": "label"})
    assert get(testset_id=tid)["score"] is None


def test_training_log_signals(monkeypatch):
    tid = make_testset(ROWS, PERFECT)
    evaluate(tid, monkeypatch, PERFECT)
    flat = [(float(0.2 + 0.8 * np.exp(-0.3 * i)), float(0.25 + 0.8 * np.exp(-0.3 * i))) for i in range(60)]
    body = get(testset_id=tid, log_id=log_id(flat))
    names = {c["name"] for c in body["components"]}
    assert {"convergence", "generalization"} <= names and body["coverage"] == pytest.approx(0.9)  # no leakage check yet
    assert body["estimate"]["status"] == "already_flat"
    assert any("flattened" in a["text"] for a in body["advice"])

    young = [(float(0.2 + 0.8 * np.exp(-0.3 * i)),) * 2 for i in range(10)]
    body = get(testset_id=tid, log_id=log_id(young))
    assert any("Keep training" in a["text"] and "range" in a["text"] for a in body["advice"])
    conv = next(c for c in body["components"] if c["name"] == "convergence")
    assert conv["value"] < 1.0


def test_overfitting_log_advice(monkeypatch):
    rows = [(1.0, 1.1), (0.7, 0.8), (0.5, 0.6), (0.4, 0.5), (0.3, 0.45), (0.2, 0.47), (0.15, 0.55),
            (0.1, 0.65), (0.07, 0.8), (0.05, 0.95)]
    body = get(log_id=log_id(rows))
    assert body["score"] is None  # no test-set evaluation yet
    texts = " ".join(a["text"] for a in body["advice"])
    assert "stop early" in texts and "beats validation" in texts
