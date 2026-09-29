"""수십만 행에서 조회가 느려지지 않는지 직접 확인하는 스크립트.

    python scripts/bench.py            # 300,000행 (약 34년치 hourly)
    python scripts/bench.py 1000000

별도 DB(bench.db)를 쓰므로 개발 DB를 건드리지 않는다. 각 쿼리의 실행 시간과 SQLite 실행 계획을 출력한다.
실행 계획에 'SEARCH ... USING (PRIMARY KEY|INDEX)'가 나오면 인덱스를 탄 것,
'SCAN observations'만 나오면 테이블 전체를 읽은 것이다.
"""
import math
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB_PATH = ROOT / "bench.db"
os.environ["HINDSIGHT_DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"

from sqlalchemy import func, select, text  # noqa: E402

from app import models  # noqa: E402,F401
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Observation, Series, User  # noqa: E402
from app.services.history import upsert_observations  # noqa: E402
from app.sources.base import Point  # noqa: E402


def timed(label, fn):
    t0 = time.perf_counter()
    result = fn()
    print(f"  {label:<44} {(time.perf_counter() - t0) * 1000:8.1f} ms")
    return result


def explain(db, sql: str, **params):
    plan = db.execute(text("EXPLAIN QUERY PLAN " + sql), params).all()
    for row in plan:
        print(f"      plan: {row[-1]}")


def main(n_rows: int) -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    with SessionLocal() as db:
        user = User(email="bench@example.com", password_hash="x")
        db.add(user)
        db.flush()
        # 다른 시리즈도 섞어 둬야 'series_id 조건'의 효과가 보인다
        series = [Series(owner_id=user.id, name=f"s{i}", frequency="hourly") for i in range(3)]
        db.add_all(series)
        db.commit()

        end = datetime(2026, 1, 1)
        start = end - timedelta(hours=n_rows - 1)
        print(f"inserting {n_rows:,} rows x {len(series)} series ...")
        t0 = time.perf_counter()
        for s in series:
            pts = [Point(start + timedelta(hours=i), 10 + 5 * math.sin(i / 24 * 2 * math.pi)) for i in range(n_rows)]
            upsert_observations(db, s.id, pts)
        db.commit()
        print(f"  done in {time.perf_counter() - t0:.1f}s\n")

        sid = series[1].id
        week_start, week_end = end - timedelta(days=200), end - timedelta(days=193)

        print("queries:")
        timed("range: 1 week of one series", lambda: db.execute(
            select(Observation.ts, Observation.value).where(
                Observation.series_id == sid, Observation.ts >= week_start, Observation.ts < week_end)
        ).all())
        explain(db, "SELECT ts, value FROM observations WHERE series_id=:s AND ts>=:a AND ts<:b",
                s=sid, a=week_start, b=week_end)

        timed("latest point (MAX ts)", lambda: db.scalar(
            select(func.max(Observation.ts)).where(Observation.series_id == sid)))
        explain(db, "SELECT max(ts) FROM observations WHERE series_id=:s", s=sid)

        timed("last 500 values (ORDER BY ts DESC LIMIT)", lambda: db.scalars(
            select(Observation.value).where(Observation.series_id == sid)
            .order_by(Observation.ts.desc()).limit(500)).all())

        year_start = end - timedelta(days=365)
        timed("daily stats for 1 year (GROUP BY)", lambda: db.execute(
            select(func.strftime("%Y-%m-%d", Observation.ts), func.avg(Observation.value), func.count())
            .where(Observation.series_id == sid, Observation.ts >= year_start, Observation.ts < end)
            .group_by(func.strftime("%Y-%m-%d", Observation.ts))).all())

        print("\ncounter-example (no usable index):")
        timed("filter on value only -> full scan", lambda: db.scalar(
            select(func.count()).select_from(Observation).where(Observation.value > 14.9)))
        explain(db, "SELECT count(*) FROM observations WHERE value > 14.9")

    engine.dispose()
    print(f"\n(bench DB: {DB_PATH.name} - delete it when done)")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 300_000)
