import csv
import io
import uuid
from collections import Counter

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

router = APIRouter(prefix="/testsets", tags=["testsets"])

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 50_000
MAX_CLASSES = 50  # a target with more distinct values than this looks like regression, not classification

# In-memory store until the PostgreSQL schema lands. Keyed by test set id.
_testsets: dict[str, dict] = {}


class ColumnInfo(BaseModel):
    name: str
    unique: int
    missing: int


class TestSetOut(BaseModel):
    id: str
    filename: str
    rows: int
    columns: list[ColumnInfo]
    target: str | None
    classes: dict[str, int] | None


class TargetIn(BaseModel):
    column: str


class LeakageOut(BaseModel):
    train_rows: int
    test_rows: int
    overlap_rows: int  # test rows that also appear in the training file
    overlap_pct: float
    duplicate_test_rows: int  # test rows repeated inside the test file itself
    warning: str | None


async def _read_csv(file: UploadFile) -> tuple[list[str], list[tuple[str, ...]]]:
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"File is larger than {MAX_BYTES // (1024 * 1024)} MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "File is not valid UTF-8 text")
    try:
        reader = csv.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or not any(h.strip() for h in header):
            raise HTTPException(422, "CSV is empty or has no header row")
        header = [h.strip() for h in header]
        if len(set(header)) != len(header):
            raise HTTPException(422, "CSV has duplicate column names")
        rows = []
        for row in reader:
            if not row:
                continue  # blank line
            if len(row) != len(header):
                raise HTTPException(
                    422, f"Row {len(rows) + 2} has {len(row)} fields, expected {len(header)}"
                )
            rows.append(tuple(c.strip() for c in row))
            if len(rows) > MAX_ROWS:
                raise HTTPException(413, f"CSV has more than {MAX_ROWS} rows")
    except csv.Error as e:
        raise HTTPException(422, f"Could not parse CSV: {e}")
    if not rows:
        raise HTTPException(422, "CSV has a header but no data rows")
    return header, rows


def get_testset(testset_id: str) -> dict:
    ts = _testsets.get(testset_id)
    if ts is None:
        raise HTTPException(404, "Test set not found")
    return ts


def _out(ts: dict) -> TestSetOut:
    return TestSetOut(
        id=ts["id"],
        filename=ts["filename"],
        rows=len(ts["rows"]),
        columns=ts["columns"],
        target=ts["target"],
        classes=ts["classes"],
    )


@router.post("", response_model=TestSetOut, status_code=201)
async def upload_testset(file: UploadFile):
    header, rows = await _read_csv(file)
    columns = []
    for i, name in enumerate(header):
        values = [r[i] for r in rows]
        columns.append(
            ColumnInfo(name=name, unique=len(set(values)), missing=sum(1 for v in values if v == ""))
        )
    ts = {
        "id": uuid.uuid4().hex,
        "filename": file.filename or "testset.csv",
        "header": header,
        "rows": rows,
        "columns": columns,
        "target": None,
        "classes": None,
    }
    _testsets[ts["id"]] = ts
    return _out(ts)


@router.get("/{testset_id}", response_model=TestSetOut)
def read_testset(testset_id: str):
    return _out(get_testset(testset_id))


@router.put("/{testset_id}/target", response_model=TestSetOut)
def set_target(testset_id: str, body: TargetIn):
    ts = get_testset(testset_id)
    if body.column not in ts["header"]:
        raise HTTPException(422, f"No column named '{body.column}'")
    idx = ts["header"].index(body.column)
    labels = [r[idx] for r in ts["rows"]]
    if "" in labels:
        raise HTTPException(422, "Target column has missing values; fill or remove those rows")
    counts = Counter(labels)
    if len(counts) < 2:
        raise HTTPException(422, "Target column has only one distinct value")
    if len(counts) > MAX_CLASSES:
        raise HTTPException(
            422,
            f"Target column has {len(counts)} distinct values; "
            "only classification targets are supported",
        )
    ts["target"] = body.column
    ts["classes"] = dict(counts.most_common())
    return _out(ts)


@router.post("/{testset_id}/leakage", response_model=LeakageOut)
async def check_leakage(testset_id: str, train_file: UploadFile):
    """Compare a training CSV against the test set; shared rows mean the metrics will be inflated."""
    ts = get_testset(testset_id)
    train_header, train_rows = await _read_csv(train_file)
    if train_header != ts["header"]:
        raise HTTPException(422, "Training CSV must have the same columns as the test set")

    train_set = set(train_rows)
    test_rows = ts["rows"]
    overlap = sum(1 for r in test_rows if r in train_set)
    duplicates = len(test_rows) - len(set(test_rows))
    pct = round(100 * overlap / len(test_rows), 1)

    warning = None
    if overlap:
        warning = (
            f"{overlap} of {len(test_rows)} test rows ({pct}%) also appear in the training data. "
            "Metrics on this test set will look better than real-world performance."
        )
    return LeakageOut(
        train_rows=len(train_rows),
        test_rows=len(test_rows),
        overlap_rows=overlap,
        overlap_pct=pct,
        duplicate_test_rows=duplicates,
        warning=warning,
    )
