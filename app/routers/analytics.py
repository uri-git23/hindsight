from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_owned_series
from app.models import Series
from app.schemas import (
    ErrorByStepRow, ErrorTrendRow, ForecastPointRow, ForecastPointsOut, LatestForecastOut, LatestForecastPoint,
)
from app.services import analytics
from app.timeutil import to_utc_naive

router = APIRouter(prefix="/series/{series_id}/analytics", tags=["analytics"])


def _utc(dt: datetime | None) -> datetime | None:
    return to_utc_naive(dt) if dt else None


@router.get("/forecast-points", response_model=ForecastPointsOut)
def forecast_points(
    step: int = Query(1, ge=1, description="몇 스텝 앞 예측을 볼지"),
    start: datetime | None = None, end: datetime | None = None,
    series: Series = Depends(get_owned_series), db: Session = Depends(get_db),
):
    rows, truncated = analytics.forecast_points(db, series.id, step=step, start=_utc(start), end=_utc(end))
    return ForecastPointsOut(
        step=step, truncated=truncated,
        points=[ForecastPointRow(model_id=r.model_id, target_ts=r.target_ts, yhat=r.yhat, actual=r.actual) for r in rows],
    )


@router.get("/error-trend", response_model=list[ErrorTrendRow])
def error_trend(
    bucket: Literal["hour", "day", "month"] = "day",
    step: int | None = Query(None, ge=1),
    start: datetime | None = None, end: datetime | None = None,
    series: Series = Depends(get_owned_series), db: Session = Depends(get_db),
):
    rows = analytics.error_trend(db, series.id, bucket=bucket, step=step, start=_utc(start), end=_utc(end))
    return [ErrorTrendRow(bucket=r.bucket, model_id=r.model_id, n=r.n, mae=r.mae) for r in rows]


@router.get("/error-by-step", response_model=list[ErrorByStepRow])
def error_by_step(
    start: datetime | None = None, end: datetime | None = None,
    series: Series = Depends(get_owned_series), db: Session = Depends(get_db),
):
    rows = analytics.error_by_step(db, series.id, start=_utc(start), end=_utc(end))
    return [ErrorByStepRow(model_id=r.model_id, step=r.step, n=r.n, mae=r.mae) for r in rows]


@router.get("/latest-forecasts", response_model=list[LatestForecastOut])
def latest_forecasts(series: Series = Depends(get_owned_series), db: Session = Depends(get_db)):
    return [
        LatestForecastOut(
            model_id=run.model_id, run_id=run.id, origin=run.origin,
            points=[LatestForecastPoint(step=p.step, target_ts=p.target_ts, yhat=p.yhat) for p in run.points],
        )
        for run in analytics.latest_runs(db, series.id)
    ]
