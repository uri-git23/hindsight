"""스케줄러 진입점.

운영: API와 별도 프로세스로 띄운다 → API를 여러 대로 늘려도 주기 작업은 한 번만 돈다.
    python -m app.scheduler
개발: HINDSIGHT_EMBEDDED_SCHEDULER=1 이면 API 프로세스 안에서 백그라운드 스레드로 돈다.
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import BaseScheduler
from apscheduler.schedulers.blocking import BlockingScheduler

from app import jobs

# max_instances=1 : 이전 회차가 아직 돌고 있으면 겹쳐 실행하지 않는다
# coalesce=True   : 서버가 멈춰 여러 회차를 놓쳤어도 재개 시 한 번만 실행한다
JOB_DEFAULTS = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 60}


def register_jobs(scheduler: BaseScheduler) -> None:
    scheduler.add_job(jobs.ingest_all, "interval", minutes=60, id="ingest_all")
    # 수집 직후 새 origin으로 예측을 봉인해 둔다 → 한 시간 뒤 실제값이 오면 score_all이 채점
    scheduler.add_job(jobs.forecast_live, "interval", minutes=15, id="forecast_live")
    scheduler.add_job(jobs.score_all, "interval", minutes=10, id="score_all")
    scheduler.add_job(jobs.train_queued, "interval", minutes=1, id="train_queued")
    scheduler.add_job(jobs.recover_stale, "interval", minutes=5, id="recover_stale")


def start_background() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC", job_defaults=JOB_DEFAULTS)
    register_jobs(scheduler)
    scheduler.start()
    return scheduler


def main() -> None:
    from app.db import Base, engine
    import app.models  # noqa: F401  (테이블 등록)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    Base.metadata.create_all(engine)
    scheduler = BlockingScheduler(timezone="UTC", job_defaults=JOB_DEFAULTS)
    register_jobs(scheduler)
    logging.getLogger("hindsight").info("scheduler started: %s", [j.id for j in scheduler.get_jobs()])
    scheduler.start()


if __name__ == "__main__":
    main()
