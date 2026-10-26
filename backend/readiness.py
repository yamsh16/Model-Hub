from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from estimate import EstimateOut, estimate_remaining
from logs import get_log
from testsets import get_testset

router = APIRouter(prefix="/readiness", tags=["readiness"])

# How much each signal counts. Missing signals are left out and the rest are re-weighted,
# and the response says how much of the total weight was actually available.
WEIGHTS = {"quality": 0.40, "weakest_class": 0.15, "convergence": 0.25, "generalization": 0.10, "data_trust": 0.10}
MIN_COVERAGE_ROWS = 30  # below this many scored rows, metrics are too noisy to lean on
WEAK_RECALL = 0.6
UNPARSED_WARN = 0.05
IMBALANCE_SHARE = 0.05


class Component(BaseModel):
    name: str
    label: str
    value: float  # 0..1
    weight: float
    detail: str


class Advice(BaseModel):
    priority: int  # 1 = do first
    text: str


class ReadinessOut(BaseModel):
    score: int | None  # 0..100; None until a test-set evaluation exists
    verdict: str
    coverage: float  # share of the total weight we had evidence for
    components: list[Component]
    advice: list[Advice]
    estimate: EstimateOut | None


def _verdict(score: int) -> str:
    if score >= 85:
        return "Close to production-ready"
    if score >= 70:
        return "Promising, a few gaps"
    if score >= 50:
        return "Needs more work"
    return "Not ready"


def _last(values: list[float | None]) -> float | None:
    return next((v for v in reversed(values) if v is not None), None)


def _convergence(est: EstimateOut) -> tuple[float, str] | None:
    if est.status == "already_flat":
        return 1.0, f"{est.series} has levelled off"
    if est.status == "estimate":
        trained = est.last_x / (est.last_x + est.remaining) if est.last_x + est.remaining > 0 else 1.0
        return max(0.2, min(1.0, trained)), (
            f"about {est.remaining:.3g} more {est.x_name}s until {est.series} flattens"
        )
    if est.status == "not_reached":
        return 0.2, f"{est.series} is still falling steadily"
    if est.status == "rising":
        return 0.6, f"{est.series} has started rising (possible overfitting)"
    return None  # unreliable: don't score what we can't judge


@router.get("", response_model=ReadinessOut)
def readiness(testset_id: str | None = None, log_id: str | None = None):
    if testset_id is None and log_id is None:
        raise HTTPException(422, "Give a testset_id, a log_id, or both")
    ts = get_testset(testset_id) if testset_id else None
    log = get_log(log_id) if log_id else None
    ev = ts["evaluation"] if ts else None
    leak = ts["leakage"] if ts else None

    comps: list[Component] = []
    advice: list[Advice] = []

    def add(name, label, value, detail):
        comps.append(Component(name=name, label=label, value=round(max(0.0, min(1.0, value)), 3),
                               weight=WEIGHTS[name], detail=detail))

    def tip(priority, text):
        advice.append(Advice(priority=priority, text=text))

    # ---- test-set evaluation ----
    if ev:
        add("quality", "Macro F1 on the test set", ev["f1_macro"],
            f"macro F1 {ev['f1_macro']:.2f}, accuracy {ev['accuracy']:.1%} on {ev['n']} rows")
        scored = [c for c in ev["per_class"] if c["support"] > 0]
        weakest = min(scored, key=lambda c: c["recall"])
        add("weakest_class", "Weakest class recall", weakest["recall"],
            f"'{weakest['label']}' recall {weakest['recall']:.2f} ({weakest['support']} rows)")

        majority = max(c["support"] for c in scored) / sum(c["support"] for c in scored)
        if ev["accuracy"] <= majority + 0.05:
            tip(2, f"Accuracy {ev['accuracy']:.0%} is barely above always guessing the most common "
                   f"class ({majority:.0%}). Check the prompt/model before tuning anything else.")
        if weakest["recall"] < WEAK_RECALL:
            tip(3, f"Weakest on '{weakest['label']}' (recall {weakest['recall']:.2f}, "
                   f"{weakest['support']} rows): add more labelled examples of it.")
        # Most common confusion between two real classes.
        labels, cm = ev["classes"], ev["confusion_matrix"]
        pairs = [(cm[i][j], labels[i], labels[j]) for i in range(len(labels)) for j in range(len(labels))
                 if i != j and labels[j] != "(unparseable)"]
        if pairs and max(pairs)[0] >= 2:
            k, true_l, pred_l = max(pairs)
            tip(4, f"Most often mistakes '{true_l}' for '{pred_l}' ({k} rows): "
                   "look at those rows for what they share.")
        if ev["unparsed"] / ev["n"] > UNPARSED_WARN:
            tip(2, f"{ev['unparsed']} of {ev['n']} replies didn't name a single class. Make the model "
                   "answer with just the class name (system prompt / format) before judging quality.")
        if ev["n"] < MIN_COVERAGE_ROWS:
            tip(5, f"Scores come from only {ev['n']} rows and are noisy; use a larger test set.")
        elif ev["sampled"]:
            tip(6, f"Scored a random {ev['n']} of {ev['total_rows']} rows; scores are estimates.")
        small = [c for c in scored if c["support"] / ev["n"] < IMBALANCE_SHARE]
        if small:
            tip(6, f"Class '{small[0]['label']}' is under {IMBALANCE_SHARE:.0%} of the test rows, so its "
                   "scores are unreliable; add test examples for it.")
    else:
        tip(1, "Run the test set through your endpoint (Evaluate) to get accuracy and a readiness score.")

    # ---- data trust ----
    if leak:
        add("data_trust", "Train/test separation", 1 - leak["overlap_pct"] / 10,
            f"{leak['overlap_rows']} test rows ({leak['overlap_pct']}%) also in training data")
        if leak["overlap_rows"]:
            tip(1, f"{leak['overlap_rows']} test rows also appear in the training data, so the scores "
                   "above are inflated. Remove them from the test set and re-run Evaluate.")
    elif ev:
        tip(7, "Upload your training CSV to check the test set for leakage; "
               "without it the scores can't be fully trusted.")

    # ---- training curve ----
    est = None
    if log:
        name = log["val_loss"] or log["train_loss"]
        if name:
            est = estimate_remaining(log["x"], log["series"][name], name, log["x_name"])
            conv = _convergence(est)
            if conv:
                add("convergence", "Training convergence", conv[0], conv[1])
            if est.status == "estimate" and est.remaining > 0:
                span = (f"about {est.remaining:.3g}" if est.remaining_low is None or est.remaining_high is None
                        else f"about {est.remaining:.3g} (range {est.remaining_low:.3g} to {est.remaining_high:.3g})")
                tip(3, f"Keep training: {span} more {est.x_name}s before {est.series} flattens.")
            elif est.status == "already_flat":
                tip(3, f"{est.series} has flattened; more {est.x_name}s alone won't help. Change the data, "
                       "model size or learning rate instead.")
            elif est.status == "not_reached":
                tip(3, f"{est.series} is still falling with no sign of flattening: keep training.")
            elif est.status == "rising":
                tip(2, f"{est.series} has been rising: stop early and use the checkpoint where it was "
                       "lowest, then add data or regularization.")
            else:
                tip(5, "The training log is too short or noisy to estimate how much training is left.")

        tr, va = log["train_loss"], log["val_loss"]
        if tr and va and tr != va:
            t_last, v_last = _last(log["series"][tr]), _last(log["series"][va])
            if t_last is not None and v_last is not None and v_last > 0:
                gap = max(0.0, (v_last - t_last) / v_last)
                add("generalization", "Train vs validation loss", 1 - min(gap / 0.5, 1.0),
                    f"validation loss is {gap:.0%} above training loss")
                if gap > 0.25:
                    tip(3, f"Training loss beats validation by {gap:.0%}: add data or regularization "
                           "before training further.")
        elif est is not None:
            tip(7, "Log both training and validation loss to check for overfitting.")

    # ---- combine ----
    total = sum(c.weight for c in comps)
    coverage = round(total / sum(WEIGHTS.values()), 2)
    score = None
    if ev and total > 0:
        score = round(100 * sum(c.value * c.weight for c in comps) / total)
        verdict = _verdict(score)
        if coverage < 0.7:
            verdict += f" (based on {len(comps)} of {len(WEIGHTS)} signals)"
    else:
        verdict = "Run an evaluation to get a score"

    advice.sort(key=lambda a: a.priority)
    return ReadinessOut(score=score, verdict=verdict, coverage=coverage, components=comps,
                        advice=advice, estimate=est)
