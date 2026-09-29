from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_owned_series
from app.errors import Conflict, InvalidInput, LookaheadViolation, SourceError
from app.models import IngestRun, Observation, Series, User
from app.schemas import (
    BucketStat, IngestRunOut, ObservationOut, ObservationPage, ObservationsIn, ObservationsWriteOut,
    SeriesCreate, SeriesOut, SeriesSummary, SeriesUpdate,
)
from app.security import get_current_user
from app.services.history import upsert_observations
from app.services.ingest import ingest_series
from app.sources import Point, validate_source
from app.timeutil import format_utc, is_on_grid, to_utc_naive, utcnow

router = APIRouter(prefix="/series", tags=["series"])


# ---------------------------------------------------------------- CRUD
@router.post("", response_model=SeriesOut, status_code=201)
def create_series(body: SeriesCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    config = validate_source(body.source, body.source_config, body.frequency)
    series = Series(owner_id=user.id, name=body.name, frequency=body.frequency, source=body.source,
                    source_config=config)
    db.add(series)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict(f"you already have a series named '{body.name}'", code="duplicate_series")
    return series


@router.get("", response_model=list[SeriesOut])
def list_series(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Series).where(Series.owner_id == user.id).order_by(Series.id)).all()


@router.get("/{series_id}", response_model=SeriesOut)
def get_series(series: Series = Depends(get_owned_series)):
    return series


@router.get("/{series_id}/summary", response_model=SeriesSummary)
def series_summary(series: Series = Depends(get_owned_series), db: Session = Depends(get_db)):
    count, first, last = db.execute(
        select(func.count(), func.min(Observation.ts), func.max(Observation.ts))
        .where(Observation.series_id == series.id)
    ).one()
    return SeriesSummary(count=count, first_ts=first, last_ts=last)


@router.patch("/{series_id}", response_model=SeriesOut)
def update_series(body: SeriesUpdate, series: Series = Depends(get_owned_series), db: Session = Depends(get_db)):
    if body.name is not None:
        series.name = body.name
    if body.source is not None or body.source_config is not None:
        # 소스 교체: 쌓인 관측치는 그대로 두고, 다음 수집부터 새 소스를 쓴다
        new_source = body.source if body.source is not None else series.source
        new_config = body.source_config if body.source_config is not None else (
            series.source_config if new_source == series.source else {}
        )
        series.source_config = validate_source(new_source, new_config, series.frequency)
        series.source = new_source
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict(f"you already have a series named '{body.name}'", code="duplicate_series")
    return series


@router.delete("/{series_id}", status_code=204)
def delete_series(series: Series = Depends(get_owned_series), db: Session = Depends(get_db)):
    # 관측치·모델·예측은 FK ON DELETE CASCADE로 DB가 같이 지운다 (수십만 행을 ORM으로 하나씩 지우지 않음)
    db.execute(delete(Series).where(Series.id == series.id))
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- observations
@router.post("/{series_id}/observations", response_model=ObservationsWriteOut)
def add_observations(body: ObservationsIn, series: Series = Depends(get_owned_series), db: Session = Depends(get_db)):
    now = utcnow()
    points = []
    for p in body.points:
        if p.ts > now:
            raise LookaheadViolation(f"observation at {format_utc(p.ts)} is in the future", code="future_observation")
        if not is_on_grid(p.ts, series.frequency):
            raise InvalidInput(f"{format_utc(p.ts)} is not aligned to the {series.frequency} grid", code="off_grid")
        points.append(Point(p.ts, p.value))
    inserted = upsert_observations(db, series.id, points)
    db.commit()
    return ObservationsWriteOut(received=len(points), inserted=inserted, duplicates=len(points) - inserted)


@router.get("/{series_id}/observations", response_model=ObservationPage)
def list_observations(
    start: datetime | None = None,
    end: datetime | None = Query(None, description="미포함"),
    after: datetime | None = Query(None, description="이전 페이지의 next_cursor"),
    limit: int = Query(1000, ge=1, le=5000),
    series: Series = Depends(get_owned_series),
    db: Session = Depends(get_db),
):
    """키셋 페이지네이션: OFFSET 대신 'ts > 마지막으로 본 ts'. 몇 페이지째든 인덱스로 바로 점프한다."""
    q = select(Observation.ts, Observation.value).where(Observation.series_id == series.id)
    if start:
        q = q.where(Observation.ts >= to_utc_naive(start))
    if end:
        q = q.where(Observation.ts < to_utc_naive(end))
    if after:
        q = q.where(Observation.ts > to_utc_naive(after))
    rows = db.execute(q.order_by(Observation.ts).limit(limit + 1)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return ObservationPage(
        items=[ObservationOut(ts=r.ts, value=r.value) for r in rows],
        next_cursor=rows[-1].ts if has_more else None,
    )


BUCKET_SECONDS = {"hour": 3600, "day": 86400, "month": 86400 * 31}
MAX_BUCKETS = 10_000


def _bucket_expr(db: Session, bucket: str):
    if db.get_bind().dialect.name == "sqlite":
        fmt = {"hour": "%Y-%m-%dT%H:00", "day": "%Y-%m-%d", "month": "%Y-%m"}[bucket]
        return func.strftime(fmt, Observation.ts)
    fmt = {"hour": 'YYYY-MM-DD"T"HH24:00', "day": "YYYY-MM-DD", "month": "YYYY-MM"}[bucket]
    return func.to_char(func.date_trunc(bucket, Observation.ts), fmt)


@router.get("/{series_id}/observations/stats", response_model=list[BucketStat])
def observation_stats(
    start: datetime,
    end: datetime,
    bucket: Literal["hour", "day", "month"] = "day",
    series: Series = Depends(get_owned_series),
    db: Session = Depends(get_db),
):
    """기간 집계는 DB에서 GROUP BY로. 수십만 행을 파이썬으로 가져와 계산하지 않는다."""
    start, end = to_utc_naive(start), to_utc_naive(end)
    if end <= start:
        raise InvalidInput("end must be after start")
    if (end - start).total_seconds() / BUCKET_SECONDS[bucket] > MAX_BUCKETS:
        raise InvalidInput(f"too many buckets; use a coarser bucket or a shorter range (max {MAX_BUCKETS})")

    b = _bucket_expr(db, bucket).label("bucket")
    rows = db.execute(
        select(b, func.count(), func.avg(Observation.value), func.min(Observation.value), func.max(Observation.value))
        .where(Observation.series_id == series.id, Observation.ts >= start, Observation.ts < end)
        .group_by(b)
        .order_by(b)
    ).all()
    return [BucketStat(bucket=r[0], count=r[1], avg=r[2], min=r[3], max=r[4]) for r in rows]


# ---------------------------------------------------------------- ingest
@router.post("/{series_id}/ingest", response_model=IngestRunOut)
def ingest_now(series: Series = Depends(get_owned_series), db: Session = Depends(get_db)):
    run = ingest_series(db, series)
    if run.status == "failed":
        raise SourceError(f"ingest run {run.id} failed: {run.error}")
    return run


@router.get("/{series_id}/ingest-runs", response_model=list[IngestRunOut])
def list_ingest_runs(
    limit: int = Query(20, ge=1, le=200), series: Series = Depends(get_owned_series), db: Session = Depends(get_db)
):
    return db.scalars(
        select(IngestRun).where(IngestRun.series_id == series.id).order_by(IngestRun.started_at.desc()).limit(limit)
    ).all()
