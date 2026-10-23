import pytest
from fastapi.testclient import TestClient

import endpoints
import logs
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    logs._logs.clear()
    monkeypatch.setattr(endpoints, "check_url_is_public", lambda url: None)


def upload(content, name="log.csv"):
    return client.post("/logs", files={"file": (name, content)})


def test_keras_style_csv():
    r = upload("epoch,loss,accuracy,val_loss,val_accuracy\n0,1.0,0.5,1.2,0.4\n1,0.6,0.7,0.9,0.6\n")
    assert r.status_code == 201
    b = r.json()
    assert b["x_name"] == "epoch" and b["x"] == [0, 1]
    assert b["series"]["loss"] == [1.0, 0.6] and "epoch" not in b["series"]
    assert (b["train_loss"], b["val_loss"]) == ("loss", "val_loss")
    assert client.get(f"/logs/{b['id']}").json() == b


def test_wandb_export_style_csv():
    r = upload('Step,train/loss,eval/loss,Name\n10,2.0,2.2,run\n20,1.5,1.9,run\n')
    b = r.json()
    assert b["x_name"] == "step" and b["x"] == [10, 20]
    assert (b["train_loss"], b["val_loss"]) == ("train/loss", "eval/loss")
    assert "Name" not in b["series"]  # text column dropped


def test_missing_cells_become_null_and_no_x_column_uses_row_index():
    b = upload("loss,val_loss\n1.0,\n0.5,nan\n0.2,0.4\n").json()
    assert b["x_name"] == "row" and b["x"] == [0, 1, 2]
    assert b["series"]["val_loss"] == [None, None, 0.4]


def test_no_loss_columns_gives_no_roles():
    b = upload("epoch,accuracy\n1,0.5\n2,0.6\n").json()
    assert b["train_loss"] is None and b["val_loss"] is None


@pytest.mark.parametrize(
    "content",
    ["epoch,note\n1,a\n2,b\n", "epoch,loss\n1,1\nx,2\n", "", "loss\n"],
)
def test_unusable_logs_rejected(content):
    assert upload(content).status_code == 422


def test_unknown_log_404():
    assert client.get("/logs/nope").status_code == 404


@pytest.mark.parametrize(
    "names,expected",
    [
        (["loss", "val_loss"], ("loss", "val_loss")),
        (["train_loss", "train_loss_epoch", "validation_loss"], ("train_loss", "validation_loss")),
        (["eval_loss", "test_loss"], (None, "eval_loss")),
        (["interval_loss"], ("interval_loss", None)),  # "val" inside a word is not validation
        (["accuracy"], (None, None)),
    ],
)
def test_detect_loss_roles(names, expected):
    assert logs.detect_loss_roles(names) == expected


# ---- MLflow import ----

RUN = {"run": {"data": {"metrics": [{"key": "accuracy"}, {"key": "val_loss"}, {"key": "loss"}]}}}
HISTORY = {
    "loss": [(0, 1.0), (1, 0.7), (2, 0.5)],
    "val_loss": [(0, 1.2), (2, 0.8)],  # missing step 1
    "accuracy": [(0, 0.4), (1, 0.6), (2, 0.7)],
}


class FakeResp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status
        self.is_success = 200 <= status < 300

    def json(self):
        if self._d is None:
            raise ValueError("not json")
        return self._d


def fake_mlflow(monkeypatch, run=RUN, status=200, calls=None):
    def get(url, params=None, headers=None, **k):
        if calls is not None:
            calls.append((url, params, headers))
        if status != 200:
            return FakeResp({}, status)
        if url.endswith("/runs/get"):
            return FakeResp(run)
        key = params["metric_key"]
        return FakeResp({"metrics": [{"key": key, "value": v, "step": s} for s, v in HISTORY[key]]})

    monkeypatch.setattr(logs.httpx, "get", get)


def mlflow(**over):
    body = {"tracking_uri": "https://mlflow.example.com/", "run_id": "abc123", **over}
    return client.post("/logs/mlflow", json=body)


def test_mlflow_import_aligns_steps_and_sends_token(monkeypatch):
    calls = []
    fake_mlflow(monkeypatch, calls=calls)
    r = mlflow(token="secret")
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["source"] == "mlflow" and b["x"] == [0, 1, 2]
    assert b["series"]["val_loss"] == [1.2, None, 0.8]
    assert (b["train_loss"], b["val_loss"]) == ("loss", "val_loss")
    assert calls[0][0] == "https://mlflow.example.com/api/2.0/mlflow/runs/get"
    assert all(c[2] == {"Authorization": "Bearer secret"} for c in calls)
    assert "secret" not in r.text


def test_mlflow_errors(monkeypatch):
    fake_mlflow(monkeypatch, status=401)
    assert mlflow().status_code == 400
    fake_mlflow(monkeypatch, status=404)
    assert mlflow().status_code == 404
    fake_mlflow(monkeypatch, status=500)
    assert mlflow().status_code == 502
    fake_mlflow(monkeypatch, run={"run": {"data": {"metrics": []}}})
    assert mlflow().status_code == 422
    assert mlflow(tracking_uri="ftp://x").status_code == 422


def test_mlflow_uri_goes_through_ssrf_guard(monkeypatch):
    monkeypatch.undo()
    monkeypatch.delenv("ALLOW_PRIVATE_ENDPOINTS", raising=False)
    assert mlflow(tracking_uri="http://127.0.0.1:5000").status_code == 422
