"""모델 학습: 상태 전이 + 백그라운드 실행.

    queued ──claim──▶ training ──▶ done
       ▲                  │
       └──── retry ◀── failed

- API는 모델을 queued로 저장하고 곧바로 202를 돌려준다. 실제 학습은 run_training()이 나중에 한다.
- run_training()은 누가 불러도 안전하다: 'queued → training'을 조건부 UPDATE로 선점(claim)하므로
  API 백그라운드 태스크와 스케줄러가 동시에 같은 모델을 잡아도 한 쪽만 실제로 학습한다.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.errors import InsufficientData, InvalidInput, InvalidState, LookaheadViolation
from app.methods import METHODS, Method
from app.models import Series, TrainedModel
from app.services.history import count_points, latest_ts, load_values
from app.timeutil import utcnow

log = logging.getLogger("hindsight.training")

TRANSITIONS: dict[str, set[str]] = {
    "queued": {"training"},
    "training": {"done", "failed"},
    "failed": {"queued"},
    "done": set(),
}


def transition(model: TrainedModel, to: str) -> None:
    if to not in TRANSITIONS[model.status]:
        raise InvalidState(f"cannot move model {model.id} from '{model.status}' to '{to}'")
    model.status = to


def get_method(name: str) -> Method:
    try:
        return METHODS[name]
    except KeyError:
        raise InvalidInput(f"unknown method '{name}'. available: {sorted(METHODS)}", code="unknown_method")


def queue_model(
    db: Session, series: Series, *, owner_id: int, name: str, method_name: str, params: dict,
    train_cutoff: datetime | None,
) -> TrainedModel:
    """요청을 검증하고 queued 모델을 만든다. 뻔히 실패할 요청(데이터 부족 등)은 큐에 넣기 전에 바로 거절."""
    method = get_method(method_name)
    try:
        params = method.validate_params(params, series.frequency)
    except ValueError as e:
        raise InvalidInput(str(e), code="invalid_params")

    now = utcnow()
    requested = train_cutoff or now
    if requested > now:
        raise LookaheadViolation(
            f"train_cutoff {requested.isoformat()} is in the future; models may only learn from data that exists now"
        )
    # cutoff는 '실제로 존재하는 마지막 관측 시각'으로 맞춘다 → 이후 예측 origin 비교가 격자 위에서 이뤄진다
    cutoff = latest_ts(db, series.id, until=requested)
    if cutoff is None:
        raise InsufficientData(f"series {series.id} has no observations at or before {requested.isoformat()}")
    n = count_points(db, series.id, cutoff)
    need = method.min_points(params)
    if n < need:
        raise InsufficientData(f"'{method.name}' needs at least {need} points up to cutoff, got {n}")

    model = TrainedModel(
        owner_id=owner_id, series_id=series.id, name=name, method=method.name, params=params,
        train_cutoff=cutoff, status="queued",
    )
    db.add(model)
    db.commit()
    return model


def retry_model(db: Session, model: TrainedModel) -> TrainedModel:
    transition(model, "queued")
    model.error = None
    model.started_at = model.finished_at = None
    db.commit()
    return model


def run_training(model_id: int) -> None:
    """백그라운드에서 실행된다. 요청 스코프의 DB 세션이 이미 닫혔으므로 자기 세션을 연다."""
    with SessionLocal() as db:
        claimed = db.execute(
            update(TrainedModel)
            .where(TrainedModel.id == model_id, TrainedModel.status == "queued")
            .values(status="training", started_at=utcnow(), error=None)
        ).rowcount
        db.commit()
        if claimed != 1:
            return  # 이미 다른 워커가 가져갔거나 queued가 아님

        model = db.get(TrainedModel, model_id)
        try:
            method = get_method(model.method)
            y = load_values(db, model.series_id, until=model.train_cutoff)
            need = method.min_points(model.params)
            if len(y) < need:
                raise InsufficientData(f"needs at least {need} points, got {len(y)}")
            model.state = method.fit(y, model.params)
            model.n_train = len(y)
            transition(model, "done")
        except Exception as e:
            log.exception("training failed for model %s", model_id)
            db.rollback()
            model = db.get(TrainedModel, model_id)
            model.error = f"{type(e).__name__}: {e}"
            transition(model, "failed")
        model.finished_at = utcnow()
        db.commit()


def train_queued_models(limit: int = 20) -> int:
    """스케줄러용: 서버 재시작 등으로 백그라운드 태스크가 유실된 queued 모델을 줍는다."""
    with SessionLocal() as db:
        ids = db.scalars(
            select(TrainedModel.id).where(TrainedModel.status == "queued").order_by(TrainedModel.id).limit(limit)
        ).all()
    for model_id in ids:
        run_training(model_id)
    return len(ids)


def fail_stale_training(max_age: timedelta = timedelta(minutes=30)) -> int:
    """training에서 너무 오래 멈춘 모델 = 워커가 죽은 것. failed로 돌려 retry할 수 있게 한다."""
    with SessionLocal() as db:
        n = db.execute(
            update(TrainedModel)
            .where(TrainedModel.status == "training", TrainedModel.started_at < utcnow() - max_age)
            .values(status="failed", error="stale: worker stopped while training", finished_at=utcnow())
        ).rowcount
        db.commit()
    return n
