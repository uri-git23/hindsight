import pytest

from app.db import SessionLocal
from app.services.scoring import score_pending
from app.timeutil import format_utc
from tests.conftest import hours_ago, make_series, train


def test_origin_before_cutoff_is_lookahead(client, auth):
    """'미래 데이터로 학습된 모델'을 과거 시점 예측에 쓰려는 경우."""
    sid, ts = make_series(client, auth, [float(i) for i in range(50)])
    m = train(client, auth, sid, cutoff=ts[40])
    r = client.post("/forecasts", json={"model_id": m["id"], "origin": format_utc(ts[20]), "horizon": 5}, headers=auth)
    assert r.status_code == 422
    assert r.json()["code"] == "lookahead_violation"

    r = client.post("/forecasts", json={"model_id": m["id"], "origin": format_utc(ts[40]), "horizon": 5}, headers=auth)
    assert r.status_code == 201


def test_future_origin_rejected(client, auth):
    sid, _ = make_series(client, auth, [1.0] * 10)
    m = train(client, auth, sid)
    r = client.post("/forecasts", json={"model_id": m["id"], "origin": format_utc(hours_ago(-2)), "horizon": 1},
                    headers=auth)
    assert r.json()["code"] == "lookahead_violation"


def test_forecast_uses_only_data_up_to_origin(client, auth):
    sid, ts = make_series(client, auth, [float(i) for i in range(50)])
    m = train(client, auth, sid, method="naive", cutoff=ts[10])
    run = client.post("/forecasts", json={"model_id": m["id"], "origin": format_utc(ts[30]), "horizon": 3},
                      headers=auth).json()
    assert [p["yhat"] for p in run["points"]] == [30.0, 30.0, 30.0]  # ts[31] 이후 값은 안 봤다
    assert [p["target_ts"] for p in run["points"]] == [format_utc(t) for t in ts[31:34]]


def test_duplicate_origin_conflict(client, auth):
    sid, _ = make_series(client, auth, [1.0] * 10)
    m = train(client, auth, sid)
    assert client.post("/forecasts", json={"model_id": m["id"], "horizon": 2}, headers=auth).status_code == 201
    r = client.post("/forecasts", json={"model_id": m["id"], "horizon": 2}, headers=auth)
    assert r.status_code == 409 and r.json()["code"] == "duplicate_forecast"


def test_scoring_is_delayed_and_idempotent(client, auth):
    # 관측은 100시간 전에 끝남. 마지막 시점에서 예측 3스텝 → 목표 시각은 지났지만 실제값이 아직 없음
    sid, ts = make_series(client, auth, [10.0] * 20)
    m = train(client, auth, sid)
    run = client.post("/forecasts", json={"model_id": m["id"], "horizon": 3}, headers=auth).json()
    assert {p["status"] for p in run["points"]} == {"missing_actual"}

    r = client.post(f"/series/{sid}/score", headers=auth).json()
    assert r == {"newly_scored": 0, "awaiting_actual": 0, "missing_actual": 3}

    # 실제값 2개가 늦게 도착
    late = [{"ts": run["points"][0]["target_ts"], "value": 12.0}, {"ts": run["points"][1]["target_ts"], "value": 7.0}]
    client.post(f"/series/{sid}/observations", json={"points": late}, headers=auth)

    r = client.post(f"/series/{sid}/score", headers=auth).json()
    assert r["newly_scored"] == 2 and r["missing_actual"] == 1
    # 다시 돌려도 아무 일도 없다
    assert client.post(f"/series/{sid}/score", headers=auth).json()["newly_scored"] == 0

    pts = client.get(f"/forecasts/{run['id']}", headers=auth).json()["points"]
    assert [p["status"] for p in pts] == ["scored", "scored", "missing_actual"]
    assert [p["abs_error"] for p in pts[:2]] == [pytest.approx(2.0), pytest.approx(3.0)]


def test_scoring_batch_function_directly(client, auth):
    sid, ts = make_series(client, auth, [float(i) for i in range(30)])
    m = train(client, auth, sid, cutoff=ts[10])
    client.post("/forecasts", json={"model_id": m["id"], "origin": format_utc(ts[20]), "horizon": 5}, headers=auth)
    with SessionLocal() as db:
        assert score_pending(db) == 5
        db.commit()
        assert score_pending(db) == 0


def test_withdraw_rules(client, auth):
    # 실제값을 알 수 있는 시점(과거)의 예측은 철회 불가
    sid, ts = make_series(client, auth, [1.0] * 30, end_hours_ago=0)
    m = train(client, auth, sid, cutoff=ts[5])
    past = client.post("/forecasts", json={"model_id": m["id"], "origin": format_utc(ts[10]), "horizon": 2},
                       headers=auth).json()
    r = client.delete(f"/forecasts/{past['id']}", headers=auth)
    assert r.status_code == 409 and r.json()["code"] == "forecast_sealed"

    # 목표 시각이 아직 안 온 라이브 예측은 철회 가능
    live = client.post("/forecasts", json={"model_id": m["id"], "horizon": 3}, headers=auth).json()
    assert live["points"][0]["status"] == "awaiting_actual"
    assert client.delete(f"/forecasts/{live['id']}", headers=auth).status_code == 204
