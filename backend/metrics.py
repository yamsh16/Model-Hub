from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from auth import current_user
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
    roc_auc_score,
)

router = APIRouter(prefix="/metrics", tags=["metrics"], dependencies=[Depends(current_user)])


class MetricsIn(BaseModel):
    y_true: list[str]
    y_pred: list[str]
    # Optional class probabilities, one row per sample, columns ordered like `classes`.
    # Endpoints that only return a label can't supply this, so ROC-AUC is then null.
    y_proba: list[list[float]] | None = None
    classes: list[str] | None = None


class ClassMetrics(BaseModel):
    label: str
    precision: float
    recall: float
    f1: float
    support: int


class MetricsOut(BaseModel):
    n: int
    classes: list[str]
    accuracy: float
    f1_macro: float
    f1_weighted: float
    roc_auc: float | None
    per_class: list[ClassMetrics]
    confusion_matrix: list[list[int]]  # rows = true class, columns = predicted class


def compute_metrics(
    y_true: list[str],
    y_pred: list[str],
    y_proba: list[list[float]] | None = None,
    classes: list[str] | None = None,
) -> MetricsOut:
    if not y_true:
        raise HTTPException(422, "No predictions to score")
    if len(y_true) != len(y_pred):
        raise HTTPException(422, "y_true and y_pred must be the same length")

    labels = classes if classes is not None else sorted(set(y_true) | set(y_pred))
    if len(set(labels)) != len(labels):
        raise HTTPException(422, "classes contains duplicates")
    unknown = (set(y_true) | set(y_pred)) - set(labels)
    if unknown:
        raise HTTPException(422, f"Labels not in classes: {sorted(unknown)}")

    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, zero_division=0
    )
    per_class = [
        ClassMetrics(
            label=lab, precision=float(p), recall=float(r), f1=float(f), support=int(s)
        )
        for lab, p, r, f, s in zip(labels, precision, recall, f1, support)
    ]

    return MetricsOut(
        n=len(y_true),
        classes=labels,
        accuracy=float(accuracy_score(y_true, y_pred)),
        # Macro-average only over classes that occur in y_true. A class that was merely predicted
        # (e.g. the "(unparseable)" bucket in evaluation) would otherwise add a spurious 0.
        f1_macro=_macro_f1(per_class),
        f1_weighted=float(
            f1_score(y_true, y_pred, labels=labels, average="weighted", zero_division=0)
        ),
        roc_auc=_roc_auc(y_true, y_proba, labels),
        per_class=per_class,
        confusion_matrix=confusion_matrix(y_true, y_pred, labels=labels).tolist(),
    )


def _macro_f1(per_class: list[ClassMetrics]) -> float:
    scored = [c.f1 for c in per_class if c.support > 0]
    return sum(scored) / len(scored) if scored else 0.0


def _roc_auc(
    y_true: list[str], y_proba: list[list[float]] | None, labels: list[str]
) -> float | None:
    if y_proba is None:
        return None
    if len(y_proba) != len(y_true) or any(len(row) != len(labels) for row in y_proba):
        raise HTTPException(422, "y_proba must have one row per sample and one column per class")
    present = set(y_true)
    if len(present) < 2:
        return None  # AUC is undefined when the test set contains one class
    try:
        if len(labels) == 2:
            positive = [1 if t == labels[1] else 0 for t in y_true]
            return float(roc_auc_score(positive, [row[1] for row in y_proba]))
        if present != set(labels):
            return None  # one-vs-rest needs every class represented in y_true
        # Rows must sum to 1 for multiclass; normalise so slightly-off probabilities still work.
        rows = [[v / (sum(row) or 1.0) for v in row] for row in y_proba]
        return float(
            roc_auc_score(y_true, rows, multi_class="ovr", average="macro", labels=labels)
        )
    except ValueError as e:
        raise HTTPException(422, f"Could not compute ROC-AUC: {e}")


@router.post("", response_model=MetricsOut)
def metrics(body: MetricsIn):
    return compute_metrics(body.y_true, body.y_pred, body.y_proba, body.classes)
