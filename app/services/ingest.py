"""외부 소스 → observations. 어떤 소스인지는 모른다(DataSource 인터페이스만 사용)."""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError, Conflict, InvalidState
from app.models import IngestRun, Series
from app.services.history import latest_ts, upsert_observations
from app.sources import MANUAL, DataSource, get_source
from app.timeutil import utcnow

# 이 시간 안에 시작된 running 수집이 있으면 새 수집을 막는다. 그보다 오래됐으면 죽은 작업으로 보고 무시.
RUNNING_LOCK_WINDOW = timedelta(minutes=10)


def ingest_series(db: Session, series: Series, source: DataSource | None = None) -> IngestRun:
    """수집 1회. 실패해도 예외 대신 status='failed'인 IngestRun을 돌려준다(기록이 남아야 하므로)."""
    if series.source == MANUAL:
        raise InvalidState("manual series is filled via POST /series/{id}/observations", code="manual_series")

    now = utcnow()
    # 중복 수집 방지 (1) 같은 시리즈 동시 수집 차단. 경합으로 둘 다 통과해도 (2) PK 충돌 무시가 막아준다.
    busy = db.scalar(
        select(IngestRun.id).where(
            IngestRun.series_id == series.id,
            IngestRun.status == "running",
            IngestRun.started_at > now - RUNNING_LOCK_WINDOW,
        )
    )
    if busy:
        raise Conflict(f"ingest {busy} is already running for this series", code="ingest_in_progress")

    run = IngestRun(series_id=series.id, source=series.source, status="running", started_at=now)
    db.add(run)
    db.commit()

    try:
        src = source or get_source(series.source)
        since = latest_ts(db, series.id)  # 증분 수집: 마지막으로 가진 시각 이후만 요청
        points = src.fetch(series.source_config, series.frequency, since, now)
        # 소스가 뭘 주든 서버 규칙으로 한 번 더 거른다: 미래 시각 금지
        points = [p for p in points if p.ts <= now]
        run.fetched = len(points)
        run.inserted = upsert_observations(db, series.id, points)
        run.status = "success"
    except AppError as e:
        db.rollback()
        run.status, run.error = "failed", f"{e.code}: {e.message}"
    except Exception as e:  # 예상 못한 버그도 run을 running으로 방치하지 않는다
        db.rollback()
        run.status, run.error = "failed", f"unexpected: {e!r}"
    run.finished_at = utcnow()
    db.add(run)
    db.commit()
    return run
