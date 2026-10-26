import numpy as np
import pytest
from fastapi.testclient import TestClient

import estimate
import logs
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh():
    logs._logs.clear()


def expo(n, k=0.3, a=0.8, c=0.2):
    return list(range(n)), [float(c + a * np.exp(-k * i)) for i in range(n)]


def run(x, y, series="val_loss"):
    return estimate.estimate_remaining(x, y, series, "epoch")


def test_recovers_asymptote_and_predicts_remaining_epochs():
    x, y = expo(10)  # still falling steeply
    e = run(x, y)
    assert e.status == "estimate" and e.model == "exponential"
    assert e.asymptote == pytest.approx(0.2, abs=0.01)
    assert e.r2 > 0.99 and e.remaining > 0
    # Truth: extending the same curve, gains flatten at about the predicted epoch.
    xs, ys = expo(80)
    window = max(0.1 * 9, 1.0)
    truth = next(i for i in range(9, 80) if ys[i] - np.interp(i + window, xs, ys) < 0.01 * ys[i])
    assert e.plateau_x == pytest.approx(truth, abs=1.5)


def test_more_training_means_less_remaining():
    assert run(*expo(10)).remaining > run(*expo(14)).remaining


def test_already_flat():
    e = run(*expo(60))
    assert e.status == "already_flat" and e.remaining == 0


def test_noisy_power_law_still_estimates():
    rng = np.random.default_rng(0)
    y = [float(0.3 + 2 * (i + 1) ** -0.7 + rng.normal(0, 0.005)) for i in range(25)]
    e = run(list(range(25)), y)
    assert e.status in ("estimate", "already_flat") and e.asymptote == pytest.approx(0.3, abs=0.1)


def test_rising_validation_loss_is_reported_not_extrapolated():
    e = run(list(range(10)), [1, 0.7, 0.5, 0.4, 0.35, 0.33, 0.34, 0.37, 0.41, 0.46])
    assert e.status == "rising" and e.remaining is None
    assert "overfitting" in e.warnings[0]


def test_too_few_points_unreliable():
    e = run([0, 1, 2], [1.0, 0.8, 0.7])
    assert e.status == "unreliable" and e.remaining is None


def test_gaps_are_ignored_and_poor_fit_is_flagged():
    x = list(range(12))
    y = [1.0, None, 0.9, 0.5, None, 0.8, 0.4, 0.75, 0.3, 0.7, None, 0.25]
    e = run(x, y)
    assert e.n_points == 9
    assert e.status in ("unreliable", "not_reached", "estimate") and e.warnings


def test_never_flattens_within_horizon():
    y = [1.0 - 0.01 * i for i in range(12)]  # a straight line down
    e = run(list(range(12)), y)
    assert e.status in ("not_reached", "unreliable", "estimate")
    if e.status == "not_reached":
        assert e.remaining is None


def test_http_route_picks_val_loss_and_validates():
    x, y = expo(8)
    csv = "epoch,loss,val_loss\n" + "".join(f"{i},{v},{v + 0.1}\n" for i, v in zip(x, y))
    lid = client.post("/logs", files={"file": ("l.csv", csv)}).json()["id"]
    r = client.get(f"/logs/{lid}/estimate")
    assert r.status_code == 200 and r.json()["series"] == "val_loss"
    assert client.get(f"/logs/{lid}/estimate", params={"series": "loss"}).json()["series"] == "loss"
    assert client.get(f"/logs/{lid}/estimate", params={"series": "zzz"}).status_code == 422
    assert client.get("/logs/nope/estimate").status_code == 404

    no_loss = client.post("/logs", files={"file": ("l.csv", "epoch,acc\n" + "".join(f"{i},0.{i}\n" for i in range(8)))})
    assert client.get(f"/logs/{no_loss.json()['id']}/estimate").status_code == 422


def test_range_brackets_the_central_estimate():
    e = run(*expo(10))
    assert e.remaining_low is not None and e.remaining_high is not None
    assert e.remaining_low <= e.remaining <= e.remaining_high
    assert e.remaining_low < e.remaining_high  # stricter/looser "flat" rules really differ
    assert not e.high_unbounded


def test_states_its_assumptions():
    e = run(*expo(10))
    assert len(e.assumptions) >= 4 and "flat" in e.criterion


def test_slow_decay_gives_a_wide_range():
    # Slow power-law decay: the strict end of the range lies far beyond the central estimate.
    y = [float(1.0 * (i + 1) ** -0.15) for i in range(12)]
    e = run(list(range(12)), y)
    assert e.status == "estimate"
    assert e.high_unbounded or e.remaining_high > 3 * e.remaining
