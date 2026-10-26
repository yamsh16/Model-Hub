import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from main import app
from metrics import compute_metrics

client = TestClient(app)

Y_TRUE = ["cat", "cat", "dog", "dog", "dog", "cat"]
Y_PRED = ["cat", "dog", "dog", "dog", "cat", "cat"]


def test_binary_metrics_match_hand_calculation():
    m = compute_metrics(Y_TRUE, Y_PRED)
    assert m.classes == ["cat", "dog"]
    assert m.accuracy == pytest.approx(4 / 6)
    # cat: tp=2 fp=1 fn=1 -> P=R=F1=2/3; dog is symmetric
    assert m.f1_macro == pytest.approx(2 / 3)
    assert m.confusion_matrix == [[2, 1], [1, 2]]
    assert [c.support for c in m.per_class] == [3, 3]
    assert m.roc_auc is None  # no probabilities supplied


def test_binary_roc_auc_uses_second_class_as_positive():
    # dog (index 1) is positive; perfect separation -> 1.0, inverted -> 0.0
    y_true = ["cat", "cat", "dog", "dog"]
    good = [[0.9, 0.1], [0.8, 0.2], [0.3, 0.7], [0.1, 0.9]]
    bad = [[0.1, 0.9], [0.2, 0.8], [0.7, 0.3], [0.9, 0.1]]
    assert compute_metrics(y_true, y_true, good).roc_auc == pytest.approx(1.0)
    assert compute_metrics(y_true, y_true, bad).roc_auc == pytest.approx(0.0)


def test_multiclass_roc_auc_and_confusion_matrix():
    y_true = ["a", "b", "c", "a", "b", "c"]
    y_pred = ["a", "b", "c", "a", "c", "c"]
    proba = [
        [0.8, 0.1, 0.1],
        [0.1, 0.8, 0.1],
        [0.1, 0.1, 0.8],
        [0.7, 0.2, 0.1],
        [0.1, 0.4, 0.5],
        [0.1, 0.2, 0.7],
    ]
    m = compute_metrics(y_true, y_pred, proba)
    assert m.roc_auc == pytest.approx(1.0)
    assert m.confusion_matrix == [[2, 0, 0], [0, 1, 1], [0, 0, 2]]


def test_never_predicted_class_does_not_crash():
    m = compute_metrics(["a", "b", "b"], ["b", "b", "b"])
    a = m.per_class[0]
    assert (a.precision, a.recall, a.f1) == (0.0, 0.0, 0.0)


def test_roc_auc_null_when_test_set_has_one_class():
    m = compute_metrics(["a", "a"], ["a", "a"], [[0.9, 0.1], [0.8, 0.2]], classes=["a", "b"])
    assert m.roc_auc is None


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(y_true=[], y_pred=[]),
        dict(y_true=["a"], y_pred=["a", "b"]),
        dict(y_true=["a", "b"], y_pred=["a", "z"], classes=["a", "b"]),
        dict(y_true=["a", "b"], y_pred=["a", "b"], classes=["a", "a"]),
        dict(y_true=["a", "b"], y_pred=["a", "b"], y_proba=[[1.0, 0.0]]),
        dict(y_true=["a", "b"], y_pred=["a", "b"], y_proba=[[1.0], [0.0]]),
    ],
)
def test_invalid_input_rejected(kwargs):
    with pytest.raises(HTTPException) as e:
        compute_metrics(**kwargs)
    assert e.value.status_code == 422


def test_http_route():
    r = client.post("/metrics", json={"y_true": Y_TRUE, "y_pred": Y_PRED})
    assert r.status_code == 200
    assert r.json()["accuracy"] == pytest.approx(4 / 6)
    assert client.post("/metrics", json={"y_true": [], "y_pred": []}).status_code == 422


def test_macro_f1_ignores_classes_that_never_occur_in_y_true():
    # "?" is only ever predicted (like the unparseable bucket); it must not drag macro F1 down.
    m = compute_metrics(["a", "b", "a", "b"], ["a", "b", "?", "b"], classes=["a", "b", "?"])
    assert m.f1_macro == pytest.approx((2 / 3 + 1.0) / 2)
