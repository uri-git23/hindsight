from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_owned_series
from app.models import Series
from app.schemas import LeaderboardOut, ScoreOut
from app.services.leaderboard import leaderboard
from app.services.scoring import pending_counts, score_pending
from app.timeutil import to_utc_naive

router = APIRouter(prefix="/series/{series_id}", tags=["scoring"])


@router.post("/score", response_model=ScoreOut)
def score_now(series: Series = Depends(get_owned_series), db: Session = Depends(get_db)):
    """스케줄러를 기다리지 않고 이 시리즈만 지금 채점. 여러 번 호출해도 안전(멱등)."""
    n = score_pending(db, series_ids=[series.id])
    db.commit()
    awaiting, missing = pending_counts(db, series.id)
    return ScoreOut(newly_scored=n, awaiting_actual=awaiting, missing_actual=missing)


@router.get("/leaderboard", response_model=LeaderboardOut)
def get_leaderboard(
    start: datetime | None = Query(None, description="target_ts 하한(포함)"),
    end: datetime | None = Query(None, description="target_ts 상한(미포함)"),
    step: int | None = Query(None, ge=1, description="특정 예측 거리(step)만 비교"),
    model_ids: list[int] | None = Query(None),
    common_only: bool = Query(True, description="모든 모델이 채점받은 (target_ts, step)만 비교"),
    series: Series = Depends(get_owned_series),
    db: Session = Depends(get_db),
):
    n_models, rows = leaderboard(
        db, series.id,
        start=to_utc_naive(start) if start else None, end=to_utc_naive(end) if end else None,
        step=step, model_ids=model_ids, common_only=common_only,
    )
    awaiting, missing = pending_counts(db, series.id)
    return LeaderboardOut(series_id=series.id, common_only=common_only, n_models=n_models,
                          awaiting_actual=awaiting, missing_actual=missing, rows=rows)
