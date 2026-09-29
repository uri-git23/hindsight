"""주기 작업들. 인자 없는 평범한 함수 → 스케줄러, CLI, 테스트 어디서든 그냥 호출할 수 있다.

API 코드는 이 파일을 모르고, 이 파일은 FastAPI를 모른다. 둘 다 services/만 사용한다.
"""
import logging

from sqlalchemy import select

from app.db import SessionLocal
from app.errors import AppError
from app.models import Series
from app.services.forecasting import make_live_forecasts
from app.services.ingest import ingest_series
from app.services.scoring import score_pending
from app.services.training import fail_stale_training, train_queued_models
from app.sources import MANUAL

log = logging.getLogger("hindsight.jobs")


def ingest_all() -> None:
    with SessionLocal() as db:
        series_list = db.scalars(select(Series).where(Series.source != MANUAL)).all()
        for series in series_list:
            try:
                run = ingest_series(db, series)
                log.info("ingest series=%s status=%s inserted=%s error=%s",
                         series.id, run.status, run.inserted, run.error)
            except AppError as e:  # 예: ingest_in_progress → 이번 회차는 건너뜀
                log.warning("ingest series=%s skipped: %s", series.id, e.message)


def forecast_live() -> None:
    with SessionLocal() as db:
        n = make_live_forecasts(db)
    if n:
        log.info("created %s live forecasts", n)


def score_all() -> None:
    with SessionLocal() as db:
        n = score_pending(db)
        db.commit()
    log.info("scored %s forecasts", n)


def train_queued() -> None:
    n = train_queued_models()
    if n:
        log.info("picked up %s queued models", n)


def recover_stale() -> None:
    n = fail_stale_training()
    if n:
        log.warning("marked %s stale training models as failed", n)
