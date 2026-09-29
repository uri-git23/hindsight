import pytest

from app.db import SessionLocal
from app.services.forecasting import make_live_forecasts
from app.timeutil import format_utc
from tests.conftest import make_series, train


def _setup(client, auth):
    sid, ts = make_series(client, auth, [2.0 * i for i in range(60)])
    naive = train(client, auth, sid, method="naive", cutoff=ts[20])
    drift = train(client, auth, sid, method="drift", cutoff=ts[20])
    for m in (naive, drift):
        client.post(f"/models/{m['id']}/backtest",
                    json={"start": format_utc(ts[20]), "end": format_utc(ts[59]), "every": 1, "horizon": 3},
                    headers=auth)
    return sid, ts, naive, drift


def test_forecast_points_by_step(client, auth):
    sid, ts, naive, drift = _setup(client, auth)
    r = client.get(f"/series/{sid}/analytics/forecast-points", params={"step": 2}, headers=auth).json()
    assert r["step"] == 2 and not r["truncated"]
    naive_pts = [p for p in r["points"] if p["model_id"] == naive["id"]]
    # origin ts[20..57]에서 2스텝 앞 → 목표 ts[22..59] (채점된 것만)
    assert [p["target_ts"] for p in naive_pts] == [format_utc(t) for t in ts[22:60]]
    assert all(p["actual"] - p["yhat"] == pytest.approx(4.0) for p in naive_pts)


def test_error_by_step_and_trend(client, auth):
    sid, ts, naive, drift = _setup(client, auth)
    rows = client.get(f"/series/{sid}/analytics/error-by-step", headers=auth).json()
    naive_mae = {r["step"]: r["mae"] for r in rows if r["model_id"] == naive["id"]}
    assert naive_mae == {1: pytest.approx(2.0), 2: pytest.approx(4.0), 3: pytest.approx(6.0)}
    assert all(r["mae"] == pytest.approx(0.0) for r in rows if r["model_id"] == drift["id"])

    trend = client.get(f"/series/{sid}/analytics/error-trend", params={"bucket": "hour", "step": 1}, headers=auth).json()
    assert {r["model_id"] for r in trend} == {naive["id"], drift["id"]}
    assert all(r["n"] == 1 for r in trend)


def test_latest_forecasts(client, auth):
    sid, ts, naive, drift = _setup(client, auth)
    latest = client.get(f"/series/{sid}/analytics/latest-forecasts", headers=auth).json()
    assert {x["model_id"] for x in latest} == {naive["id"], drift["id"]}
    assert all(x["origin"] == format_utc(ts[59]) and len(x["points"]) == 3 for x in latest)


def test_analytics_respects_ownership(client, auth):
    from tests.conftest import auth_headers
    sid, *_ = _setup(client, auth)
    other = auth_headers(client, "other@example.com")
    for path in ("forecast-points", "error-trend", "error-by-step", "latest-forecasts"):
        assert client.get(f"/series/{sid}/analytics/{path}", headers=other).status_code == 404


def test_live_forecasts_are_idempotent(client, auth):
    sid, ts = make_series(client, auth, [1.0] * 30)
    train(client, auth, sid, method="naive")
    train(client, auth, sid, method="mean")
    with SessionLocal() as db:
        assert make_live_forecasts(db) == 2
        assert make_live_forecasts(db) == 0  # 새 관측치가 없으면 origin이 같아 건너뜀
    runs = client.get("/forecasts", params={"series_id": sid}, headers=auth).json()
    assert len(runs) == 2 and all(r["horizon"] == 24 for r in runs)
