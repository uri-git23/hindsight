import httpx
import pytest

from app.db import SessionLocal
from app.models import IngestRun, Series
from app.services.ingest import ingest_series
from app.sources.open_meteo import OpenMeteoSource
from app.timeutil import utcnow


def _create(client, auth, **body):
    r = client.post("/series", json={"name": "s", "frequency": "hourly", **body}, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_synthetic_ingest_is_incremental_and_dedups(client, auth):
    sid = _create(client, auth, source="synthetic", source_config={"backfill": 48})
    first = client.post(f"/series/{sid}/ingest", headers=auth).json()
    assert first["status"] == "success" and first["inserted"] == 48

    second = client.post(f"/series/{sid}/ingest", headers=auth).json()
    assert second["inserted"] == 0  # 같은 시간 안에 다시 수집해도 중복 없음

    runs = client.get(f"/series/{sid}/ingest-runs", headers=auth).json()
    assert len(runs) == 2


def test_manual_series_cannot_ingest(client, auth):
    sid = _create(client, auth)
    r = client.post(f"/series/{sid}/ingest", headers=auth)
    assert r.status_code == 409 and r.json()["code"] == "manual_series"


def test_concurrent_ingest_blocked(client, auth):
    sid = _create(client, auth, source="synthetic")
    with SessionLocal() as db:
        db.add(IngestRun(series_id=sid, source="synthetic", status="running", started_at=utcnow()))
        db.commit()
    r = client.post(f"/series/{sid}/ingest", headers=auth)
    assert r.status_code == 409 and r.json()["code"] == "ingest_in_progress"


def _open_meteo_series(client, auth) -> int:
    return _create(client, auth, source="open_meteo", source_config={"latitude": 37.57, "longitude": 126.98})


def test_open_meteo_timeout_records_failed_run(client, auth):
    sid = _open_meteo_series(client, auth)
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("slow", request=request)

    src = OpenMeteoSource(transport=httpx.MockTransport(handler), attempts=3, backoff=0)
    with SessionLocal() as db:
        run = ingest_series(db, db.get(Series, sid), source=src)
    assert run.status == "failed" and "timeout" in run.error
    assert len(calls) == 3  # 재시도했다


def test_open_meteo_4xx_is_not_retried(client, auth):
    sid = _open_meteo_series(client, auth)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"reason": "bad variable"})

    src = OpenMeteoSource(transport=httpx.MockTransport(handler), attempts=3, backoff=0)
    with SessionLocal() as db:
        run = ingest_series(db, db.get(Series, sid), source=src)
    assert run.status == "failed" and len(calls) == 1


def test_open_meteo_filters_future_points(client, auth):
    sid = _open_meteo_series(client, auth)
    now = utcnow().replace(minute=0, second=0, microsecond=0)
    from datetime import timedelta
    times = [(now + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(-3, 4)]

    def handler(request):
        return httpx.Response(200, json={"hourly": {"time": times, "temperature_2m": [1, 2, 3, 4, 5, 6, 7]}})

    src = OpenMeteoSource(transport=httpx.MockTransport(handler), attempts=1)
    with SessionLocal() as db:
        run = ingest_series(db, db.get(Series, sid), source=src)
    assert run.status == "success"
    assert run.inserted == 4  # -3,-2,-1,0시간. 예보(미래)는 버림


def test_swap_source_keeps_data(client, auth):
    sid = _create(client, auth, source="synthetic", source_config={"backfill": 10})
    client.post(f"/series/{sid}/ingest", headers=auth)
    r = client.patch(f"/series/{sid}", json={"source": "open_meteo",
                                            "source_config": {"latitude": 1, "longitude": 2}}, headers=auth)
    assert r.status_code == 200 and r.json()["source"] == "open_meteo"
    assert len(client.get(f"/series/{sid}/observations", headers=auth).json()["items"]) == 10

    r = client.patch(f"/series/{sid}", json={"source": "open_meteo", "source_config": {}}, headers=auth)
    assert r.status_code == 422


@pytest.mark.parametrize("job", ["ingest_all", "score_all", "train_queued", "recover_stale"])
def test_jobs_run_without_api(client, auth, job):
    from app import jobs
    _create(client, auth, source="synthetic", source_config={"backfill": 5})
    getattr(jobs, job)()  # 예외 없이 돈다
