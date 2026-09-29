"""공정한 순위.

모델마다 예측한 시점이 다르면 MAE를 그냥 비교할 수 없다 (한 모델만 쉬운 구간을 맞췄을 수도 있다).
common_only=True(기본)이면 '비교 대상 모든 모델이 채점을 받은 (target_ts, step) 조합'만으로 집계한다.
같은 문제를 같은 거리(step)에서 맞힌 것끼리만 비교하는 셈.
"""
import math
from datetime import datetime

from sqlalchemy import and_, distinct, func, select
from sqlalchemy.orm import Session

from app.models import Forecast, TrainedModel
from app.schemas import LeaderboardRow


def leaderboard(
    db: Session, series_id: int, *, start: datetime | None = None, end: datetime | None = None,
    step: int | None = None, model_ids: list[int] | None = None, common_only: bool = True,
) -> tuple[int, list[LeaderboardRow]]:
    conds = [Forecast.series_id == series_id, Forecast.scored_at.is_not(None)]
    if start is not None:
        conds.append(Forecast.target_ts >= start)
    if end is not None:
        conds.append(Forecast.target_ts < end)
    if step is not None:
        conds.append(Forecast.step == step)
    if model_ids:
        conds.append(Forecast.model_id.in_(model_ids))

    scored = select(
        Forecast.model_id, Forecast.target_ts, Forecast.step, Forecast.abs_error, Forecast.sq_error,
        (Forecast.yhat - Forecast.actual).label("err"),
    ).where(*conds).subquery("scored")

    n_models = db.scalar(select(func.count(distinct(scored.c.model_id)))) or 0
    source = scored
    if common_only and n_models > 1:
        common_keys = (
            select(scored.c.target_ts, scored.c.step)
            .group_by(scored.c.target_ts, scored.c.step)
            .having(func.count(distinct(scored.c.model_id)) == n_models)
            .subquery("common_keys")
        )
        source = (
            select(*scored.c)
            .join(common_keys, and_(scored.c.target_ts == common_keys.c.target_ts, scored.c.step == common_keys.c.step))
            .subquery("common")
        )

    agg = (
        select(
            source.c.model_id,
            func.count().label("n"),
            func.avg(source.c.abs_error).label("mae"),
            func.avg(source.c.sq_error).label("mse"),
            func.avg(source.c.err).label("bias"),
        )
        .group_by(source.c.model_id)
        .subquery("agg")
    )
    rows = db.execute(
        select(agg, TrainedModel.name, TrainedModel.method)
        .join(TrainedModel, TrainedModel.id == agg.c.model_id)
        .order_by(agg.c.mae, agg.c.model_id)
    ).all()

    return n_models, [
        LeaderboardRow(
            rank=i, model_id=r.model_id, model_name=r.name, method=r.method, n=r.n,
            mae=r.mae, rmse=math.sqrt(r.mse), bias=r.bias,
        )
        for i, r in enumerate(rows, start=1)
    ]
