from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_owned_run, owned_model
from app.models import ForecastRun, User
from app.schemas import ForecastCreate, ForecastPointOut, ForecastRunOut, ForecastRunSummary
from app.security import get_current_user
from app.services.forecasting import create_forecast_run, point_status, withdraw_run
from app.timeutil import utcnow

router = APIRouter(prefix="/forecasts", tags=["forecasts"])


def _to_out(run: ForecastRun) -> ForecastRunOut:
    now = utcnow()
    return ForecastRunOut(
        id=run.id, series_id=run.series_id, model_id=run.model_id, origin=run.origin, horizon=run.horizon,
        created_at=run.created_at,
        points=[
            ForecastPointOut(step=p.step, target_ts=p.target_ts, yhat=p.yhat, actual=p.actual,
                             abs_error=p.abs_error, status=point_status(p, now))
            for p in run.points
        ],
    )


@router.post("", response_model=ForecastRunOut, status_code=201)
def create_forecast(body: ForecastCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    model = owned_model(db, user, body.model_id)
    run = create_forecast_run(db, model, origin=body.origin, horizon=body.horizon)
    db.commit()
    return _to_out(run)


@router.get("", response_model=list[ForecastRunSummary])
def list_forecasts(
    series_id: int | None = None, model_id: int | None = None, limit: int = Query(100, ge=1, le=1000),
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    q = select(ForecastRun).where(ForecastRun.owner_id == user.id)
    if series_id is not None:
        q = q.where(ForecastRun.series_id == series_id)
    if model_id is not None:
        q = q.where(ForecastRun.model_id == model_id)
    return db.scalars(q.order_by(ForecastRun.origin.desc()).limit(limit)).all()


@router.get("/{run_id}", response_model=ForecastRunOut)
def get_forecast(run: ForecastRun = Depends(get_owned_run)):
    return _to_out(run)


@router.delete("/{run_id}", status_code=204)
def delete_forecast(run: ForecastRun = Depends(get_owned_run), db: Session = Depends(get_db)):
    withdraw_run(db, run)
    return Response(status_code=204)
