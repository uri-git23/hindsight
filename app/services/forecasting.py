"""예측 생성. 서버가 직접 계산하고 봉인하므로, 클라이언트가 숫자를 조작하거나 미래를 엿볼 틈이 없다.

시점 규칙 (전부 여기서 강제)
1. origin은 '지금'보다 미래일 수 없다.
2. origin은 모델의 train_cutoff 이후여야 한다. 아니면 모델이 origin 이후(=미래) 데이터를 이미 본 것.
3. 예측 계산에는 origin(포함) 이전 관측치만 쓴다 (history.load_values).
4. 같은 (모델, origin) 예측은 한 번만. 다시 뽑아서 좋은 것만 남기는 체리피킹 방지.
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import Conflict, InsufficientData, InvalidState, LookaheadViolation
from app.models import Forecast, ForecastRun, TrainedModel
from app.services.history import latest_ts, load_values
from app.services.training import get_method
from app.timeutil import STEP, format_utc, utcnow


def ensure_ready(model: TrainedModel) -> None:
    if model.status != "done":
        raise InvalidState(
            f"model {model.id} is '{model.status}'; only 'done' models can forecast", code="model_not_ready"
        )


def create_forecast_run(
    db: Session, model: TrainedModel, *, origin: datetime | None, horizon: int, now: datetime | None = None
) -> ForecastRun:
    """커밋하지 않는다(호출자가 여러 개를 묶어 커밋할 수 있도록)."""
    ensure_ready(model)
    now = now or utcnow()
    requested = origin or now
    if requested > now:
        raise LookaheadViolation(f"origin {format_utc(requested)} is in the future")

    # origin을 '그 시점에 실제로 알고 있던 마지막 관측 시각'으로 맞춘다
    effective = latest_ts(db, model.series_id, until=requested)
    if effective is None:
        raise InsufficientData(f"no observations at or before {format_utc(requested)}")
    if effective < model.train_cutoff:
        raise LookaheadViolation(
            f"model {model.id} was trained on data up to {format_utc(model.train_cutoff)}, "
            f"but the forecast origin is {format_utc(effective)}. "
            "It has already seen what it is asked to predict; train a model with an earlier cutoff."
        )

    if db.scalar(select(ForecastRun.id).where(ForecastRun.model_id == model.id, ForecastRun.origin == effective)):
        raise Conflict(
            f"model {model.id} already has a forecast from origin {format_utc(effective)}",
            code="duplicate_forecast",
        )

    method = get_method(model.method)
    need = max(1, method.history_needed(model.params))
    history = load_values(db, model.series_id, until=effective, limit=need)
    if len(history) < need:
        raise InsufficientData(f"'{method.name}' needs {need} points up to origin, got {len(history)}")

    yhat = method.predict(model.state or {}, model.params, history, horizon)
    step = STEP[model.series.frequency]
    run = ForecastRun(
        owner_id=model.owner_id, series_id=model.series_id, model_id=model.id,
        origin=effective, horizon=horizon, created_at=now,
    )
    run.points = [
        Forecast(series_id=model.series_id, model_id=model.id, step=k, target_ts=effective + step * k, yhat=float(v))
        for k, v in enumerate(yhat, start=1)
    ]
    db.add(run)
    db.flush()
    return run


def withdraw_run(db: Session, run: ForecastRun, now: datetime | None = None) -> None:
    """예측 철회는 '정답이 아직 존재할 수 없을 때'만 허용. 결과를 보고 지우는 건 체리피킹이다."""
    now = now or utcnow()
    first_target = min(p.target_ts for p in run.points)
    if first_target <= now or any(p.scored_at is not None for p in run.points):
        raise InvalidState(
            "forecast is sealed: its first target time has passed, so it can no longer be withdrawn",
            code="forecast_sealed",
        )
    db.delete(run)
    db.commit()


def point_status(point: Forecast, now: datetime) -> str:
    if point.scored_at is not None:
        return "scored"
    return "awaiting_actual" if point.target_ts > now else "missing_actual"
