import math
import re
import uuid
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel, Field

import endpoints
from testsets import _read_csv

router = APIRouter(prefix="/logs", tags=["logs"])

MLFLOW_TIMEOUT_SECONDS = 15.0
MAX_MLFLOW_METRICS = 12  # history is fetched once per metric, so bound the fan-out

# In-memory store until the PostgreSQL schema lands. Keyed by log id.
_logs: dict[str, dict] = {}

# Column names (lower-cased) that mean "x axis". Covers Keras/PyTorch Lightning CSV loggers,
# HF Trainer, and W&B's exported "Step"/"_step".
X_NAMES = ("epoch", "step", "_step", "global_step", "iteration", "iter", "iters")
VAL_WORDS = re.compile(r"(^|[^a-z])(val|valid|validation|eval|test)([^a-z]|$)")


class LogOut(BaseModel):
    id: str
    filename: str
    source: str  # "csv" or "mlflow"
    x_name: str  # "epoch", "step", or "row" when the file had no such column
    x: list[float]
    series: dict[str, list[float | None]]  # None marks a missing/non-numeric cell
    train_loss: str | None  # which series to draw as training loss
    val_loss: str | None  # which series to draw as validation loss


class MlflowIn(BaseModel):
    tracking_uri: str
    run_id: str = Field(min_length=1, max_length=64)
    token: str | None = None  # optional bearer token for secured tracking servers


def to_float(text: str) -> float | None:
    try:
        v = float(text)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def find_x_column(names: list[str]) -> str | None:
    lowered = {n.lower(): n for n in names}
    for candidate in X_NAMES:
        if candidate in lowered:
            return lowered[candidate]
    return None


def detect_loss_roles(names: list[str]) -> tuple[str | None, str | None]:
    """Pick the training-loss and validation-loss series from column names."""
    losses = [n for n in names if "loss" in n.lower()]
    val = [n for n in losses if VAL_WORDS.search(n.lower())]
    train = [n for n in losses if n not in val]
    # Prefer the plainest name ("loss", "train_loss", "train/loss") when several match.
    train.sort(key=len)
    val.sort(key=len)
    return (train[0] if train else None, val[0] if val else None)


def _store(filename: str, source: str, x_name: str, x: list[float],
           series: dict[str, list[float | None]]) -> LogOut:
    if not series:
        raise HTTPException(422, "No numeric columns found in the training log")
    train, val = detect_loss_roles(list(series))
    log = LogOut(
        id=uuid.uuid4().hex, filename=filename, source=source, x_name=x_name, x=x,
        series=series, train_loss=train, val_loss=val,
    )
    _logs[log.id] = log.model_dump()
    return log


@router.post("", response_model=LogOut, status_code=201)
async def upload_log(file: UploadFile):
    header, rows = await _read_csv(file)
    x_col = find_x_column(header)
    columns = {h: [to_float(r[i]) for r in rows] for i, h in enumerate(header)}

    if x_col:
        xs = columns.pop(x_col)
        if any(v is None for v in xs):
            raise HTTPException(422, f"Column '{x_col}' has non-numeric or missing values")
        x_name = x_col.lower().lstrip("_")
    else:
        xs, x_name = [float(i) for i in range(len(rows))], "row"

    # Keep only columns with at least one number; text columns (run names, timestamps) drop out.
    series = {name: vals for name, vals in columns.items() if any(v is not None for v in vals)}
    return _store(file.filename or "training-log.csv", "csv", x_name, xs, series)


def _mlflow_get(base: str, path: str, params: dict, token: str | None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = httpx.get(
            f"{base}/api/2.0/mlflow/{path}", params=params, headers=headers,
            timeout=MLFLOW_TIMEOUT_SECONDS, follow_redirects=False,
        )
    except httpx.TimeoutException:
        raise HTTPException(502, "MLflow server timed out")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Could not reach MLflow server: {type(e).__name__}")
    if r.status_code in (401, 403):
        raise HTTPException(400, "MLflow server rejected the token")
    if r.status_code == 404:
        raise HTTPException(404, "MLflow run or metric not found")
    if not r.is_success:
        raise HTTPException(502, f"MLflow server returned HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise HTTPException(502, "MLflow server did not return JSON; is this the tracking URI?")


@router.post("/mlflow", response_model=LogOut, status_code=201)
def import_mlflow(body: MlflowIn):
    """Import a run's metric histories from an MLflow tracking server (REST API)."""
    base = body.tracking_uri.strip().rstrip("/")
    if not re.match(r"^https?://", base):
        raise HTTPException(422, "Tracking URI must start with http:// or https://")
    endpoints.check_url_is_public(base)  # same SSRF guard as model endpoints

    run = _mlflow_get(base, "runs/get", {"run_id": body.run_id}, body.token)
    try:
        keys = [m["key"] for m in run["run"]["data"].get("metrics", [])]
    except (KeyError, TypeError):
        raise HTTPException(502, "Unexpected response from MLflow runs/get")
    if not keys:
        raise HTTPException(422, "That MLflow run has no logged metrics")

    # Loss-like metrics first so the cap never drops the ones the chart needs.
    keys.sort(key=lambda k: ("loss" not in k.lower(), k))
    keys = keys[:MAX_MLFLOW_METRICS]

    by_key: dict[str, dict[int, float]] = {}
    for key in keys:
        hist = _mlflow_get(
            base, "metrics/get-history", {"run_id": body.run_id, "metric_key": key}, body.token
        )
        points: dict[int, float] = {}
        for m in hist.get("metrics", []):
            try:
                v = float(m["value"])
                if math.isfinite(v):
                    points[int(m.get("step", 0))] = v  # last write wins for a repeated step
            except (KeyError, TypeError, ValueError):
                continue
        if points:
            by_key[key] = points

    steps = sorted({s for pts in by_key.values() for s in pts})
    series = {k: [pts.get(s) for s in steps] for k, pts in by_key.items()}
    return _store(f"mlflow:{quote(body.run_id)}", "mlflow", "step", [float(s) for s in steps], series)


@router.get("/{log_id}", response_model=LogOut)
def read_log(log_id: str):
    log = _logs.get(log_id)
    if log is None:
        raise HTTPException(404, "Training log not found")
    return LogOut(**log)
