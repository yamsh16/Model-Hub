import random
import re
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import endpoints
from auth import current_user
from db import get_db
from metrics import MetricsOut, compute_metrics
from models import User
from testsets import class_labels, get_testset, table

router = APIRouter(prefix="/testsets", tags=["evaluation"], dependencies=[Depends(current_user)])

DEFAULT_ROWS = 100
MAX_EVAL_ROWS = 200  # runs synchronously inside the request until the task queue lands
WORKERS = 4
UNPARSED = "(unparseable)"


class EvaluateIn(BaseModel):
    endpoint_id: str
    limit: int = Field(DEFAULT_ROWS, ge=1, le=MAX_EVAL_ROWS)


class EvaluationOut(MetricsOut):
    total_rows: int  # rows in the test set
    sampled: bool  # True when only a random subset was scored
    unparsed: int  # replies that did not name exactly one class (counted as wrong)
    target: str


def build_prompt(classes: list[str], features: dict[str, str]) -> str:
    record = "\n".join(f"{k}: {v}" for k, v in features.items())
    return (
        f"Classify the record below into exactly one of these classes: {', '.join(classes)}.\n"
        "Reply with only the class name and nothing else.\n\n"
        f"Record:\n{record}"
    )


def parse_label(reply: str, classes: list[str]) -> str | None:
    """Map a free-text reply to one class, or None if it is missing or ambiguous."""
    text = reply.strip().strip("\"'`.*").strip().lower()
    for c in classes:
        if text == c.lower():
            return c
    found = [
        c for c in classes if re.search(rf"(?<!\w){re.escape(c.lower())}(?!\w)", reply.lower())
    ]
    return found[0] if len(found) == 1 else None


@router.post("/{testset_id}/evaluate", response_model=EvaluationOut)
def evaluate(
    testset_id: str, body: EvaluateIn, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    ts = get_testset(db, user, testset_id)
    if ts.target is None:
        raise HTTPException(422, "Choose a target column before evaluating")
    # Both lookups are scoped to the signed-in user: you can't score your data with someone
    # else's endpoint, or theirs with yours.
    ep = endpoints.get_endpoint(db, user, body.endpoint_id)
    endpoints.check_url_is_public(ep["url"])  # re-check: DNS can change after registration

    header, all_rows = table(ts)
    classes = class_labels(ts)
    t_idx = header.index(ts.target)
    rows = all_rows
    sampled = len(rows) > body.limit
    if sampled:
        rows = random.Random(0).sample(rows, body.limit)  # fixed seed: reruns score the same rows

    prompts = [
        build_prompt(
            classes, {h: v for i, (h, v) in enumerate(zip(header, row)) if i != t_idx}
        )
        for row in rows
    ]

    def ask(prompt: str) -> str | HTTPException:
        try:
            return endpoints.chat_completion(ep, prompt)
        except HTTPException as e:
            return e

    with ThreadPoolExecutor(WORKERS) as pool:
        replies = list(pool.map(ask, prompts))

    errors = [r for r in replies if isinstance(r, HTTPException)]
    if len(errors) == len(replies):
        raise HTTPException(errors[0].status_code, errors[0].detail)  # nothing usable came back

    y_true = [r[t_idx] for r in rows]
    y_pred = [
        UNPARSED if isinstance(rep, HTTPException) else (parse_label(rep, classes) or UNPARSED)
        for rep in replies
    ]
    unparsed = y_pred.count(UNPARSED)
    labels = classes + [UNPARSED] if unparsed else classes

    m = compute_metrics(y_true, y_pred, classes=labels)
    result = EvaluationOut(
        **m.model_dump(),
        total_rows=len(all_rows),
        sampled=sampled,
        unparsed=unparsed,
        target=ts.target,
    )
    ts.evaluation = result.model_dump()  # the readiness score reads this
    db.commit()
    return result
