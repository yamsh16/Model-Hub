import pytest
from fastapi.testclient import TestClient

import testsets
from main import app

client = TestClient(app)

TEST_CSV = "a,b,label\n1,2,cat\n3,4,dog\n5,6,cat\n5,6,cat\n"


def upload(content=TEST_CSV, name="test.csv"):
    return client.post("/testsets", files={"file": (name, content, "text/csv")})


def test_upload_reports_shape_and_columns():
    r = upload()
    assert r.status_code == 201
    body = r.json()
    assert body["rows"] == 4
    assert [c["name"] for c in body["columns"]] == ["a", "b", "label"]
    assert body["columns"][2]["unique"] == 2
    assert body["target"] is None
    assert client.get(f"/testsets/{body['id']}").json() == body


@pytest.mark.parametrize(
    "content,code",
    [
        ("", 422),
        ("a,b\n", 422),
        ("a,a\n1,2\n", 422),
        ("a,b\n1,2,3\n", 422),
        (b"\xff\xfe\x00bad", 422),
    ],
)
def test_bad_csv_rejected(content, code):
    assert upload(content).status_code == code


def test_too_large_rejected(monkeypatch):
    monkeypatch.setattr(testsets, "MAX_BYTES", 10)
    assert upload().status_code == 413


def test_select_target_returns_class_counts():
    tid = upload().json()["id"]
    r = client.put(f"/testsets/{tid}/target", json={"column": "label"})
    assert r.status_code == 200
    assert r.json()["classes"] == {"cat": 3, "dog": 1}


def test_target_validation():
    tid = upload().json()["id"]
    assert client.put(f"/testsets/{tid}/target", json={"column": "nope"}).status_code == 422
    # single class
    t2 = upload("x,y\n1,a\n2,a\n").json()["id"]
    assert client.put(f"/testsets/{t2}/target", json={"column": "y"}).status_code == 422
    # missing values
    t3 = upload("x,y\n1,a\n2,\n").json()["id"]
    assert client.put(f"/testsets/{t3}/target", json={"column": "y"}).status_code == 422
    # too many classes (looks like regression)
    big = "x,y\n" + "".join(f"{i},{i}\n" for i in range(testsets.MAX_CLASSES + 1))
    t4 = upload(big).json()["id"]
    assert client.put(f"/testsets/{t4}/target", json={"column": "y"}).status_code == 422
    assert client.put("/testsets/missing/target", json={"column": "y"}).status_code == 404


def leak(tid, content):
    return client.post(f"/testsets/{tid}/leakage", files={"train_file": ("train.csv", content)})


def test_leakage_detected():
    tid = upload().json()["id"]
    r = leak(tid, "a,b,label\n1,2,cat\n5,6,cat\n9,9,dog\n")
    assert r.status_code == 200
    body = r.json()
    assert body["overlap_rows"] == 3  # rows 1, 3 and the repeated row 4 all match
    assert body["overlap_pct"] == 75.0
    assert body["duplicate_test_rows"] == 1
    assert "75.0%" in body["warning"]


def test_no_leakage_has_no_warning():
    tid = upload().json()["id"]
    body = leak(tid, "a,b,label\n7,7,dog\n").json()
    assert body["overlap_rows"] == 0 and body["warning"] is None


def test_leakage_requires_matching_columns():
    tid = upload().json()["id"]
    assert leak(tid, "x,y,z\n1,2,3\n").status_code == 422
    assert leak("missing", TEST_CSV).status_code == 404
