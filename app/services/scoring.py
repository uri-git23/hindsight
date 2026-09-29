"""지연 채점 배치.

정답(실제값)은 예측보다 늦게 도착한다. 이 배치는 '채점 안 된 예측 중 정답이 들어온 것'만 채점한다.

멱등성: 모든 UPDATE가 `scored_at IS NULL` 조건을 가진다.
  → 몇 번을 돌려도, 두 프로세스가 동시에 돌려도 이미 채점된 행은 다시 건드리지 않는다.
  → 정답이 아직 없는 행은 그대로 남았다가 다음 실행 때 채점된다.

집합 기반 SQL 2단계 (행을 파이썬으로 끌어오지 않는다)
  1) actual 채우기  : 대응하는 관측치가 존재하는 행에만
  2) 오차 계산·확정 : actual이 채워졌는데 scored_at이 없는 행
  1과 2 사이에 죽어도 다음 실행의 2단계가 마저 처리한다.
"""
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session

from app.models import Forecast, Observation
from app.timeutil import utcnow


def score_pending(db: Session, series_ids: Sequence[int] | None = None, now: datetime | None = None) -> int:
    now = now or utcnow()
    scope = [Forecast.scored_at.is_(None)]
    if series_ids is not None:
        scope.append(Forecast.series_id.in_(series_ids))

    matching_obs = (Observation.series_id == Forecast.series_id) & (Observation.ts == Forecast.target_ts)
    db.execute(
        update(Forecast)
        .where(*scope, Forecast.actual.is_(None), Forecast.target_ts <= now, exists().where(matching_obs))
        .values(actual=select(Observation.value).where(matching_obs).scalar_subquery())
        .execution_options(synchronize_session=False)
    )
    err = Forecast.yhat - Forecast.actual
    scored = db.execute(
        update(Forecast)
        .where(*scope, Forecast.actual.is_not(None))
        .values(abs_error=func.abs(err), sq_error=err * err, scored_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    return scored


def pending_counts(db: Session, series_id: int, now: datetime | None = None) -> tuple[int, int]:
    """(awaiting_actual, missing_actual). missing = 목표 시각이 지났는데 실제값이 안 들어온 것."""
    now = now or utcnow()
    base = select(func.count()).select_from(Forecast).where(
        Forecast.series_id == series_id, Forecast.scored_at.is_(None)
    )
    awaiting = db.scalar(base.where(Forecast.target_ts > now)) or 0
    missing = db.scalar(base.where(Forecast.target_ts <= now)) or 0
    return awaiting, missing
