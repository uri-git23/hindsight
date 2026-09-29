from fastapi import APIRouter, BackgroundTasks, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_owned_model, owned_series
from app.errors import InvalidInput, InvalidState, LookaheadViolation
from app.methods import METHODS
from app.models import ForecastRun, Observation, TrainedModel, User
from app.schemas import BacktestIn, BacktestOut, MethodInfo, ModelCreate, ModelOut
from app.security import get_current_user
from app.services.forecasting import create_forecast_run, ensure_ready
from app.services.scoring import score_pending
from app.services.training import queue_model, retry_model, run_training
from app.timeutil import format_utc, utcnow

router = APIRouter(tags=["models"])

MAX_BACKTEST_ORIGINS = 1_000


@router.get("/methods", response_model=list[MethodInfo])
def list_methods():
    return [
        MethodInfo(
            name=m.name, description=m.description, params_help=m.params_help,
            default_params={f: m.default_params(f) for f in ("hourly", "daily")},
        )
        for m in METHODS.values()
    ]


@router.post("/models", response_model=ModelOut, status_code=202)
def create_model(
    body: ModelCreate, background: BackgroundTasks,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    """202 Accepted: '접수됨, 결과는 나중에'. 클라이언트는 GET /models/{id}로 status를 확인한다."""
    series = owned_series(db, user, body.series_id)
    model = queue_model(db, series, owner_id=user.id, name=body.name, method_name=body.method,
                        params=body.params, train_cutoff=body.train_cutoff)
    background.add_task(run_training, model.id)
    return model


@router.get("/models", response_model=list[ModelOut])
def list_models(
    series_id: int | None = None, status: str | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    q = select(TrainedModel).where(TrainedModel.owner_id == user.id)
    if series_id is not None:
        q = q.where(TrainedModel.series_id == series_id)
    if status is not None:
        q = q.where(TrainedModel.status == status)
    return db.scalars(q.order_by(TrainedModel.id)).all()


@router.get("/models/{model_id}", response_model=ModelOut)
def get_model(model: TrainedModel = Depends(get_owned_model)):
    return model


@router.post("/models/{model_id}/retry", response_model=ModelOut, status_code=202)
def retry(background: BackgroundTasks, model: TrainedModel = Depends(get_owned_model), db: Session = Depends(get_db)):
    retry_model(db, model)  # failed가 아니면 InvalidState(409)
    background.add_task(run_training, model.id)
    return model


@router.delete("/models/{model_id}", status_code=204)
def delete_model(model: TrainedModel = Depends(get_owned_model), db: Session = Depends(get_db)):
    if model.status == "training":
        raise InvalidState("model is training; wait until it finishes", code="model_busy")
    if db.scalar(select(ForecastRun.id).where(ForecastRun.model_id == model.id).limit(1)):
        # 예측 기록이 있는 모델을 지우면 리더보드에서 '못한 모델'을 숨길 수 있다
        raise InvalidState("model has forecasts on record and cannot be deleted", code="model_has_forecasts")
    db.delete(model)
    db.commit()
    return Response(status_code=204)


@router.post("/models/{model_id}/backtest", response_model=BacktestOut)
def backtest(body: BacktestIn, model: TrainedModel = Depends(get_owned_model), db: Session = Depends(get_db)):
    """[start, end] 사이 관측 시각들을 origin으로 삼아 과거 시점에서 예측을 몰아서 만든다(rolling origin).

    이미 있는 origin은 건너뛰므로 같은 요청을 여러 번 보내도 결과가 같다(멱등).
    """
    ensure_ready(model)
    if body.end < body.start:
        raise InvalidInput("end must not be before start")
    if body.start < model.train_cutoff:
        raise LookaheadViolation(
            f"backtest start {format_utc(body.start)} is before the model's train_cutoff "
            f"{format_utc(model.train_cutoff)}; origins there would be judged by a model that saw the answers"
        )

    now = utcnow()
    all_origins = db.scalars(
        select(Observation.ts)
        .where(Observation.series_id == model.series_id, Observation.ts >= body.start,
               Observation.ts <= min(body.end, now))
        .order_by(Observation.ts)
    ).all()
    origins = all_origins[:: body.every]
    if len(origins) > MAX_BACKTEST_ORIGINS:
        raise InvalidInput(f"{len(origins)} origins requested; max is {MAX_BACKTEST_ORIGINS}. Increase 'every'.")

    existing = set(db.scalars(select(ForecastRun.origin).where(ForecastRun.model_id == model.id)))
    created = 0
    for origin in origins:
        if origin in existing:
            continue
        create_forecast_run(db, model, origin=origin, horizon=body.horizon, now=now)
        created += 1
    db.flush()
    scored = score_pending(db, series_ids=[model.series_id], now=now)
    db.commit()
    return BacktestOut(origins=len(origins), created=created, skipped_existing=len(origins) - created, scored=scored)

