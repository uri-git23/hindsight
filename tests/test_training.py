import pytest

from app.errors import InvalidState
from app.methods import METHODS
from app.models import TrainedModel
from app.services.training import transition
from app.timeutil import format_utc
from tests.conftest import hours_ago, make_series, train


def test_training_lifecycle_done(client, auth):
    sid, ts = make_series(client, auth, [float(i) for i in range(50)])
    m = train(client, auth, sid, method="drift")
    assert m["status"] == "done"
    assert m["n_train"] == 50
    assert m["state"]["slope"] == pytest.approx(1.0)
    assert m["train_cutoff"] == format_utc(ts[-1])
    assert m["started_at"] and m["finished_at"]


def test_cutoff_limits_training_data(client, auth):
    sid, ts = make_series(client, auth, [float(i) for i in range(50)])
    m = train(client, auth, sid, method="mean", cutoff=ts[9])
    assert m["n_train"] == 10
    assert m["state"]["mean"] == pytest.approx(4.5)  # 0..9 평균. 이후 값은 보지 않았다


def test_insufficient_data_rejected_up_front(client, auth):
    sid, _ = make_series(client, auth, [1.0, 2.0, 3.0])
    r = client.post("/models", json={"series_id": sid, "name": "x", "method": "seasonal_naive"}, headers=auth)
    assert r.status_code == 422 and r.json()["code"] == "insufficient_data"
    assert "24" in r.json()["detail"]

    empty, _ = make_series(client, auth, [], name="empty")
    r = client.post("/models", json={"series_id": empty, "name": "x", "method": "naive"}, headers=auth)
    assert r.json()["code"] == "insufficient_data"


def test_future_cutoff_rejected(client, auth):
    sid, _ = make_series(client, auth, [1.0, 2.0])
    r = client.post("/models", json={"series_id": sid, "name": "x", "method": "naive",
                                     "train_cutoff": format_utc(hours_ago(-5))}, headers=auth)
    assert r.status_code == 422 and r.json()["code"] == "lookahead_violation"


def test_bad_method_and_params(client, auth):
    sid, _ = make_series(client, auth, [1.0] * 30)
    r = client.post("/models", json={"series_id": sid, "name": "x", "method": "prophet"}, headers=auth)
    assert r.json()["code"] == "unknown_method"
    r = client.post("/models", json={"series_id": sid, "name": "x", "method": "seasonal_naive",
                                     "params": {"season_length": 0}}, headers=auth)
    assert r.json()["code"] == "invalid_params"


def test_failure_then_retry(client, auth, monkeypatch):
    sid, _ = make_series(client, auth, [1.0] * 30)

    def boom(*_a, **_k):
        raise RuntimeError("numerical explosion")

    monkeypatch.setattr(METHODS["naive"], "fit", boom)
    m = train(client, auth, sid, method="naive")
    assert m["status"] == "failed"
    assert "numerical explosion" in m["error"]

    # failed 상태에선 예측 불가
    r = client.post("/forecasts", json={"model_id": m["id"], "horizon": 3}, headers=auth)
    assert r.status_code == 409 and r.json()["code"] == "model_not_ready"

    monkeypatch.undo()
    r = client.post(f"/models/{m['id']}/retry", headers=auth)
    assert r.status_code == 202
    assert client.get(f"/models/{m['id']}", headers=auth).json()["status"] == "done"

    # done에서 retry는 허용되지 않는 전이
    r = client.post(f"/models/{m['id']}/retry", headers=auth)
    assert r.status_code == 409


def test_transition_table():
    m = TrainedModel(id=1, status="queued")
    with pytest.raises(InvalidState):
        transition(m, "done")  # training을 건너뛸 수 없다
    transition(m, "training")
    transition(m, "failed")
    transition(m, "queued")
    assert m.status == "queued"


def test_claim_is_exclusive(client, auth):
    """같은 모델에 run_training이 두 번 불려도 두 번째는 아무것도 안 한다."""
    from app.services.training import run_training

    sid, _ = make_series(client, auth, [1.0] * 30)
    m = train(client, auth, sid)
    finished = m["finished_at"]
    run_training(m["id"])  # 이미 done → claim 실패 → no-op
    assert client.get(f"/models/{m['id']}", headers=auth).json()["finished_at"] == finished


def test_model_with_forecasts_cannot_be_deleted(client, auth):
    sid, _ = make_series(client, auth, [1.0] * 30)
    m = train(client, auth, sid)
    client.post("/forecasts", json={"model_id": m["id"], "horizon": 2}, headers=auth)
    r = client.delete(f"/models/{m['id']}", headers=auth)
    assert r.status_code == 409 and r.json()["code"] == "model_has_forecasts"

    m2 = train(client, auth, sid, name="unused")
    assert client.delete(f"/models/{m2['id']}", headers=auth).status_code == 204
