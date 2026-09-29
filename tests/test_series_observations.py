from datetime import timedelta

from app.timeutil import format_utc, utcnow
from tests.conftest import hours_ago, make_series


def test_series_crud(client, auth):
    r = client.post("/series", json={"name": "temp", "frequency": "hourly"}, headers=auth)
    assert r.status_code == 201
    sid = r.json()["id"]
    assert client.post("/series", json={"name": "temp", "frequency": "daily"}, headers=auth).status_code == 409

    r = client.patch(f"/series/{sid}", json={"name": "temp2"}, headers=auth)
    assert r.json()["name"] == "temp2"
    assert client.delete(f"/series/{sid}", headers=auth).status_code == 204
    assert client.get(f"/series/{sid}", headers=auth).status_code == 404


def test_invalid_source_config(client, auth):
    r = client.post("/series", json={"name": "x", "frequency": "hourly", "source": "open_meteo",
                                     "source_config": {"latitude": 999, "longitude": 0}}, headers=auth)
    assert r.status_code == 422 and r.json()["code"] == "invalid_source_config"
    r = client.post("/series", json={"name": "x", "frequency": "hourly", "source": "nope"}, headers=auth)
    assert r.json()["code"] == "unknown_source"


def test_future_and_off_grid_observations_rejected(client, auth):
    sid, _ = make_series(client, auth, [])
    future = format_utc(hours_ago(-3))
    r = client.post(f"/series/{sid}/observations", json={"points": [{"ts": future, "value": 1}]}, headers=auth)
    assert r.status_code == 422 and r.json()["code"] == "future_observation"

    off = format_utc(hours_ago(5) + timedelta(minutes=30))
    r = client.post(f"/series/{sid}/observations", json={"points": [{"ts": off, "value": 1}]}, headers=auth)
    assert r.status_code == 422 and r.json()["code"] == "off_grid"


def test_duplicate_observations_are_ignored(client, auth):
    sid, ts = make_series(client, auth, [1.0, 2.0, 3.0])
    payload = {"points": [{"ts": format_utc(ts[0]), "value": 999}, {"ts": format_utc(hours_ago(1)), "value": 4}]}
    r = client.post(f"/series/{sid}/observations", json=payload, headers=auth).json()
    assert r == {"received": 2, "inserted": 1, "duplicates": 1}
    # 먼저 들어온 값이 유지된다
    first = client.get(f"/series/{sid}/observations", params={"limit": 1}, headers=auth).json()["items"][0]
    assert first["value"] == 1.0


def test_timezone_aware_input_is_normalized(client, auth):
    sid, _ = make_series(client, auth, [])
    t = hours_ago(10)
    kst = (t + timedelta(hours=9)).isoformat() + "+09:00"
    client.post(f"/series/{sid}/observations", json={"points": [{"ts": kst, "value": 1}]}, headers=auth)
    items = client.get(f"/series/{sid}/observations", headers=auth).json()["items"]
    assert items[0]["ts"] == format_utc(t)


def test_keyset_pagination(client, auth):
    sid, _ = make_series(client, auth, [float(i) for i in range(25)])
    seen, after = [], None
    while True:
        params = {"limit": 10, **({"after": after} if after else {})}
        page = client.get(f"/series/{sid}/observations", params=params, headers=auth).json()
        seen += [p["value"] for p in page["items"]]
        after = page["next_cursor"]
        if after is None:
            break
    assert seen == [float(i) for i in range(25)]


def test_stats_bucket(client, auth):
    sid, ts = make_series(client, auth, [1.0] * 24 + [3.0] * 24, end_hours_ago=0)
    start, end = format_utc(ts[0] - timedelta(days=1)), format_utc(utcnow() + timedelta(hours=1))
    rows = client.get(f"/series/{sid}/observations/stats", params={"start": start, "end": end, "bucket": "hour"},
                      headers=auth).json()
    assert sum(r["count"] for r in rows) == 48
    r = client.get(f"/series/{sid}/observations/stats",
                   params={"start": "2000-01-01T00:00:00Z", "end": "2026-01-01T00:00:00Z", "bucket": "hour"},
                   headers=auth)
    assert r.status_code == 422
