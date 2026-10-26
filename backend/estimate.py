import warnings as _warnings
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from scipy.optimize import OptimizeWarning, curve_fit

from logs import get_log

router = APIRouter(prefix="/logs", tags=["estimate"])

MIN_POINTS = 6
GOOD_POINTS = 10
GOOD_R2 = 0.9
WINDOW_FRACTION = 0.10  # "gains" are measured over this fraction of the span trained so far
FLAT_GAIN = 0.01  # flat = loss improves by less than 1% of itself over that window
HORIZON_SPANS = 20  # give up looking this many multiples of the trained span into the future
RISING_TOLERANCE = 0.02  # last loss this far above the best => the curve has turned upward

# Stricter/looser meanings of "flat" used to build the range around the central estimate.
RANGE_GAINS = (0.02, 0.01, 0.005)
RANGE_R2_SLACK = 0.05  # a second curve family counts as plausible if its R² is this close to the best

ASSUMPTIONS = [
    "The loss keeps following the same smooth decay it has shown so far.",
    "Learning rate, batch size, data and augmentation stay as they were; a schedule change "
    "(warm restart, decay step) can break the trend.",
    "Loss is measured the same way throughout, on the same validation data.",
    "'Flat' is a rule of thumb, not a true optimum: models can still improve slowly after it.",
    "This is about loss only; accuracy on your task can behave differently.",
]

Status = Literal["already_flat", "estimate", "not_reached", "rising", "unreliable"]


class EstimateOut(BaseModel):
    series: str
    x_name: str
    model: Literal["power", "exponential"] | None
    n_points: int
    last_x: float
    r2: float | None
    fitted_loss_now: float | None
    asymptote: float | None  # loss the fitted curve levels off at
    status: Status
    remaining: float | None = None  # more x-units (epochs/steps) until gains flatten; None if unknown
    remaining_low: float | None = None  # optimistic end of the range
    remaining_high: float | None = None  # pessimistic end; None with high_unbounded = no end found
    high_unbounded: bool = False
    plateau_x: float | None = None
    assumptions: list[str] = ASSUMPTIONS
    criterion: str
    warnings: list[str]


def _power(t, a, b, c):
    return a * (t + 1.0) ** (-b) + c


def _exp(t, a, k, c):
    return a * np.exp(-k * t) + c


def _fit(model, t, y):
    """Least-squares fit; returns (params, sse) or None if the optimiser fails."""
    lo_c, hi_c = (0.0 if y.min() >= 0 else y.min() - np.ptp(y)), y.min()
    drop = max(y[0] - y.min(), 1e-9)
    if model == "power":
        f, p0 = _power, [drop, 0.5, max(lo_c, y.min() * 0.9)]
        bounds = ([0, 0.01, lo_c], [np.inf, 6.0, hi_c])
    else:
        f, p0 = _exp, [drop, 3.0 / max(t[-1], 1e-9), max(lo_c, y.min() * 0.9)]
        bounds = ([0, 1e-6, lo_c], [np.inf, np.inf, hi_c])
    try:
        with _warnings.catch_warnings():
            _warnings.simplefilter("ignore", OptimizeWarning)
            p, _ = curve_fit(f, t, y, p0=p0, bounds=bounds, maxfev=20000)
    except (RuntimeError, ValueError):
        return None
    return f, p, float(np.sum((y - f(t, *p)) ** 2))


def estimate_remaining(x: list[float], values: list[float | None], series: str, x_name: str) -> EstimateOut:
    pts = [(xi, v) for xi, v in zip(x, values) if v is not None]
    n = len(pts)
    last_x = float(max(x)) if x else 0.0
    criterion = (
        f"gains are 'flat' when {x_name}s over the next {int(WINDOW_FRACTION * 100)}% of training "
        f"so far improve the loss by less than {int(FLAT_GAIN * 100)}% of its value"
    )
    base = dict(series=series, x_name=x_name, n_points=n, last_x=last_x, criterion=criterion,
                model=None, r2=None, fitted_loss_now=None, asymptote=None)
    if n < MIN_POINTS:
        return EstimateOut(**base, status="unreliable",
                           warnings=[f"Only {n} points; at least {MIN_POINTS} are needed to fit a curve."])

    xs = np.array([p[0] for p in pts], dtype=float)
    ys = np.array([p[1] for p in pts], dtype=float)
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]
    t = xs - xs[0]
    span = float(t[-1])
    if span <= 0:
        return EstimateOut(**base, status="unreliable", warnings=["All points share one x value."])

    notes: list[str] = []
    best_i = int(np.argmin(ys))
    if ys[-1] > ys[best_i] * (1 + RISING_TOLERANCE) + 1e-12 and best_i < n - 1:
        notes.append(
            f"{series} was lowest at {x_name} {xs[best_i]:g} and has risen since; "
            "more training is unlikely to help (the model may be overfitting)."
        )
        return EstimateOut(**base, status="rising", warnings=notes)

    fits = [(m, _fit(m, t, ys)) for m in ("power", "exponential")]
    fits = [(m, r) for m, r in fits if r is not None]
    if not fits:
        return EstimateOut(**base, status="unreliable", warnings=["Could not fit a decay curve to this series."])
    model, (f, params, sse) = min(fits, key=lambda mr: mr[1][2])

    sst = float(np.sum((ys - ys.mean()) ** 2))
    r2 = 1.0 - sse / sst if sst > 0 else 0.0
    asymptote = float(params[2])
    now = float(f(span, *params))

    if r2 < GOOD_R2:
        notes.append(f"The curve fits poorly (R² = {r2:.2f}); treat the estimate as a rough guide.")
    if n < GOOD_POINTS:
        notes.append(f"Only {n} points; the estimate is sensitive to a few measurements.")

    window = max(WINDOW_FRACTION * span, span / max(n - 1, 1))
    ahead = np.linspace(span, span * (1 + HORIZON_SPANS), 4000)

    def remaining_for(fn, prm, gain):
        """x-units until `fn` is flat under `gain`; 0.0 if already flat; None if beyond the horizon."""
        cur, nxt = fn(ahead, *prm), fn(ahead + window, *prm)
        flat = (cur - nxt) < gain * np.maximum(np.abs(cur), 1e-12)
        if not flat.any():
            return None
        return float(ahead[int(np.argmax(flat))] - span)

    out = dict(base, model=model, r2=float(r2), fitted_loss_now=now, asymptote=asymptote)
    reliable = r2 >= GOOD_R2 and n >= GOOD_POINTS

    # Range: every plausible curve family x every "flat" strictness.
    candidates = []
    for m, (fn, prm, m_sse) in fits:
        m_r2 = 1.0 - m_sse / sst if sst > 0 else 0.0
        if m == model or m_r2 >= r2 - RANGE_R2_SLACK:
            candidates += [remaining_for(fn, prm, g) for g in RANGE_GAINS]
    found = [c for c in candidates if c is not None]
    rng = dict(
        remaining_low=min(found) if found else None,
        remaining_high=max(found) if found and len(found) == len(candidates) else None,
        high_unbounded=len(found) < len(candidates),
    )

    central = remaining_for(f, params, FLAT_GAIN)
    if central == 0.0:
        return EstimateOut(**out, **rng, status="already_flat" if reliable else "unreliable",
                           remaining=0.0, plateau_x=last_x, warnings=notes)
    if central is None:
        notes.append(f"Gains do not flatten within {HORIZON_SPANS}x the training so far.")
        return EstimateOut(**out, **rng, status="not_reached", warnings=notes)
    return EstimateOut(**out, **rng, status="estimate" if reliable else "unreliable",
                       remaining=central, plateau_x=float(xs[0] + span + central), warnings=notes)


@router.get("/{log_id}/estimate", response_model=EstimateOut)
def estimate(log_id: str, series: str | None = None):
    log = get_log(log_id)
    name = series or log["val_loss"] or log["train_loss"]
    if name is None:
        raise HTTPException(422, "This log has no loss column to fit")
    if name not in log["series"]:
        raise HTTPException(422, f"No series named '{name}'")
    return estimate_remaining(log["x"], log["series"][name], name, log["x_name"])
