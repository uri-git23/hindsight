import pytest

from app.timeutil import format_utc
from tests.conftest import make_series, train


def test_drift_beats_naive_on_trend(client, auth):
    sid, ts = make_series(client, auth, [2.0 * i for i in range(60)])
    naive = train(client, auth, sid, method="naive", cutoff=ts[20])
    drift = train(client, auth, sid, method="drift", cutoff=ts[20])
    for m in (naive, drift):
        r = client.post(f"/models/{m['id']}/backtest",
                        json={"start": format_utc(ts[20]), "end": format_utc(ts[50]), "every": 5, "horizon": 3},
                        headers=auth)
        assert r.status_code == 200, r.text

    board = client.get(f"/series/{sid}/leaderboard", headers=auth).json()
    assert [row["method"] for row in board["rows"]] == ["drift", "naive"]
    assert board["rows"][0]["mae"] == pytest.approx(0.0)
    assert board["rows"][1]["mae"] == pytest.approx(4.0)  # 스텝 1,2,3 오차 2,4,6 평균
    assert board["rows"][1]["bias"] < 0  # naive는 계속 과소예측

    only_step1 = client.get(f"/series/{sid}/leaderboard", params={"step": 1}, headers=auth).json()
    assert only_step1["rows"][1]["mae"] == pytest.approx(2.0)


def test_common_only_compares_same_targets(client, auth):
    sid, ts = make_series(client, auth, [float(i % 5) for i in range(80)])
    a = train(client, auth, sid, method="naive", cutoff=ts[10], name="a")
    b = train(client, auth, sid, method="mean", cutoff=ts[10], name="b")
    # a는 넓은 구간, b는 좁은 구간만 예측
    client.post(f"/models/{a['id']}/backtest",
                json={"start": format_utc(ts[10]), "end": format_utc(ts[70]), "horizon": 1}, headers=auth)
    client.post(f"/models/{b['id']}/backtest",
                json={"start": format_utc(ts[30]), "end": format_utc(ts[40]), "horizon": 1}, headers=auth)

    fair = client.get(f"/series/{sid}/leaderboard", headers=auth).json()
    assert {row["n"] for row in fair["rows"]} == {11}  # 둘 다 채점받은 목표 시각만

    unfair = client.get(f"/series/{sid}/leaderboard", params={"common_only": False}, headers=auth).json()
    assert sorted(row["n"] for row in unfair["rows"]) == [11, 61]


def test_backtest_is_idempotent_and_guards_cutoff(client, auth):
    sid, ts = make_series(client, auth, [float(i) for i in range(40)])
    m = train(client, auth, sid, cutoff=ts[10])
    body = {"start": format_utc(ts[10]), "end": format_utc(ts[30]), "every": 2, "horizon": 2}
    first = client.post(f"/models/{m['id']}/backtest", json=body, headers=auth).json()
    second = client.post(f"/models/{m['id']}/backtest", json=body, headers=auth).json()
    assert first["created"] == 11 and second["created"] == 0 and second["skipped_existing"] == 11

    early = {**body, "start": format_utc(ts[5])}
    r = client.post(f"/models/{m['id']}/backtest", json=early, headers=auth)
    assert r.status_code == 422 and r.json()["code"] == "lookahead_violation"
