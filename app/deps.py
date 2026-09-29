"""소유권 검증.

원칙: 조회 조건에 owner_id를 '같이' 건다. 먼저 id로 찾고 나중에 owner를 비교하면 실수로 빠뜨리기 쉽다.
남의 리소스는 403이 아니라 404로 응답한다 → 그 id가 존재하는지조차 알려주지 않는다.
"""
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import NotFound
from app.models import ForecastRun, Series, TrainedModel, User
from app.security import get_current_user


def owned_series(db: Session, user: User, series_id: int) -> Series:
    series = db.scalar(select(Series).where(Series.id == series_id, Series.owner_id == user.id))
    if series is None:
        raise NotFound(f"series {series_id} not found", code="series_not_found")
    return series


def owned_model(db: Session, user: User, model_id: int) -> TrainedModel:
    model = db.scalar(select(TrainedModel).where(TrainedModel.id == model_id, TrainedModel.owner_id == user.id))
    if model is None:
        raise NotFound(f"model {model_id} not found", code="model_not_found")
    return model


def owned_run(db: Session, user: User, run_id: int) -> ForecastRun:
    run = db.scalar(select(ForecastRun).where(ForecastRun.id == run_id, ForecastRun.owner_id == user.id))
    if run is None:
        raise NotFound(f"forecast run {run_id} not found", code="forecast_not_found")
    return run


# 경로 파라미터용 의존성 래퍼
def get_owned_series(series_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Series:
    return owned_series(db, user, series_id)


def get_owned_model(model_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TrainedModel:
    return owned_model(db, user, model_id)


def get_owned_run(run_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ForecastRun:
    return owned_run(db, user, run_id)
