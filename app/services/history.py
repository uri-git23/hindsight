"""관측치 읽기/쓰기. 모든 조회는 (series_id, ts) PK 인덱스를 타도록 series_id + ts 범위 조건으로만 한다."""
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import Observation
from app.sources.base import Point
from app.timeutil import utcnow

CHUNK = 10_000


def latest_ts(db: Session, series_id: int, until: datetime | None = None) -> datetime | None:
    q = select(func.max(Observation.ts)).where(Observation.series_id == series_id)
    if until is not None:
        q = q.where(Observation.ts <= until)
    return db.scalar(q)


def count_points(db: Session, series_id: int, until: datetime) -> int:
    q = select(func.count()).select_from(Observation).where(
        Observation.series_id == series_id, Observation.ts <= until
    )
    return db.scalar(q) or 0


def load_values(db: Session, series_id: int, until: datetime, limit: int | None = None) -> list[float]:
    """until(포함) 이전 값만 시간순으로. ← 시점 규칙의 핵심: 이 함수 밖에서 관측치를 읽지 않는다."""
    q = select(Observation.value).where(Observation.series_id == series_id, Observation.ts <= until)
    if limit is None:
        return list(db.scalars(q.order_by(Observation.ts)))
    newest_first = db.scalars(q.order_by(Observation.ts.desc()).limit(limit)).all()
    return list(reversed(newest_first))


def upsert_observations(db: Session, series_id: int, points: Iterable[Point]) -> int:
    """이미 있는 (series_id, ts)는 건너뛴다(ON CONFLICT DO NOTHING). 새로 들어간 행 수를 돌려준다.

    '먼저 들어온 값이 이긴다' → 한 번 채점에 쓰인 실제값이 나중에 바뀌어 순위가 흔들리는 일이 없다.
    """
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
    now = utcnow()
    rows = [{"series_id": series_id, "ts": p.ts, "value": p.value, "ingested_at": now} for p in points]
    if not rows:
        return 0
    # 파라미터 목록을 넘기면 드라이버의 executemany로 실행된다 (거대한 VALUES 문을 매번 컴파일하지 않음)
    stmt = insert(Observation.__table__).on_conflict_do_nothing(index_elements=["series_id", "ts"])
    inserted = 0
    for i in range(0, len(rows), CHUNK):
        inserted += db.connection().execute(stmt, rows[i : i + CHUNK]).rowcount
    return inserted
