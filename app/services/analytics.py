"""대시보드용 집계. 모두 DB에서 GROUP BY로 끝내고, 파이썬으로는 결과만 받는다."""
from datetime import datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import Forecast, ForecastRun

MAX_POINTS = 20_000


def bucket_expr(db: Session, bucket: str, column):
    """시각 컬럼을 hour/day/month 문자열 버킷으로. DB 종류마다 함수가 달라 여기서만 분기한다."""
    if db.get_bind().dialect.name == "sqlite":
        fmt = {"hour": "%Y-%m-%dT%H:00", "day": "%Y-%m-%d", "month": "%Y-%m"}[bucket]
        return func.strftime(fmt, column)
    fmt = {"hour": 'YYYY-MM-DD"T"HH24:00', "day": "YYYY-MM-DD", "month": "YYYY-MM"}[bucket]
    return func.to_char(func.date_trunc(bucket, column), fmt)


def _scored_conditions(series_id: int, start: datetime | None, end: datetime | None) -> list:
    conds = [Forecast.series_id == series_id, Forecast.scored_at.is_not(None)]
    if start is not None:
        conds.append(Forecast.target_ts >= start)
    if end is not None:
        conds.append(Forecast.target_ts < end)
    return conds


def forecast_points(
    db: Session, series_id: int, *, step: int, start: datetime | None = None, end: datetime | None = None,
) -> tuple[list, bool]:
    """step 스텝 앞 예측들을 목표 시각순으로 (채점된 것만). 모델마다 '실제값을 얼마나 따라갔나'를 그리는 용도."""
    rows = db.execute(
        select(Forecast.model_id, Forecast.target_ts, Forecast.yhat, Forecast.actual)
        .where(*_scored_conditions(series_id, start, end), Forecast.step == step)
        .order_by(Forecast.target_ts, Forecast.model_id)
        .limit(MAX_POINTS + 1)
    ).all()
    return rows[:MAX_POINTS], len(rows) > MAX_POINTS


def error_trend(
    db: Session, series_id: int, *, bucket: str, step: int | None = None,
    start: datetime | None = None, end: datetime | None = None,
) -> list:
    b = bucket_expr(db, bucket, Forecast.target_ts).label("bucket")
    conds = _scored_conditions(series_id, start, end)
    if step is not None:
        conds.append(Forecast.step == step)
    return db.execute(
        select(b, Forecast.model_id, func.count().label("n"), func.avg(Forecast.abs_error).label("mae"))
        .where(*conds)
        .group_by(b, Forecast.model_id)
        .order_by(b, Forecast.model_id)
    ).all()


def error_by_step(db: Session, series_id: int, *, start: datetime | None = None, end: datetime | None = None) -> list:
    return db.execute(
        select(Forecast.model_id, Forecast.step, func.count().label("n"), func.avg(Forecast.abs_error).label("mae"))
        .where(*_scored_conditions(series_id, start, end))
        .group_by(Forecast.model_id, Forecast.step)
        .order_by(Forecast.model_id, Forecast.step)
    ).all()


def latest_runs(db: Session, series_id: int) -> list[ForecastRun]:
    """모델마다 가장 최근 origin의 예측 1건 → '앞으로 어떻게 될 거라고 보는가'."""
    latest = (
        select(ForecastRun.model_id, func.max(ForecastRun.origin).label("origin"))
        .where(ForecastRun.series_id == series_id)
        .group_by(ForecastRun.model_id)
        .subquery()
    )
    return list(db.scalars(
        select(ForecastRun)
        .join(latest, and_(ForecastRun.model_id == latest.c.model_id, ForecastRun.origin == latest.c.origin))
        .order_by(ForecastRun.model_id)
    ))
