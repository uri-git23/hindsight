"""ORM 테이블 정의.

인덱스 설계 메모
- observations: PK(series_id, ts). 중복 수집 방지(유니크)와 '시리즈 + 기간' 범위 조회 인덱스를 한 번에 해결한다.
- forecasts: (series_id, target_ts) → 리더보드/조회의 기간 필터.
             target_ts WHERE scored_at IS NULL (부분 인덱스) → 채점 배치가 '아직 안 채점된 것'만 빠르게 찾는다.
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutil import utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Series(Base):
    __tablename__ = "series"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_series_owner_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    frequency: Mapped[str] = mapped_column(String(10))  # hourly | daily
    source: Mapped[str] = mapped_column(String(30), default="manual")
    source_config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Observation(Base):
    __tablename__ = "observations"

    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    value: Mapped[float] = mapped_column(Float)
    # 서버가 이 값을 '알게 된' 시각. 나중에 point-in-time 규칙을 더 엄격하게(ingested_at 기준) 바꿀 때 쓴다.
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class IngestRun(Base):
    """외부 수집 1회의 기록. 실패도 남겨야 '왜 데이터가 비었지?'를 추적할 수 있다."""
    __tablename__ = "ingest_runs"
    __table_args__ = (Index("ix_ingest_series_started", "series_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(10), default="running")  # running | success | failed
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class TrainedModel(Base):
    __tablename__ = "trained_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    method: Mapped[str] = mapped_column(String(30))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    # 이 시각(포함)까지의 관측치만 학습에 쓴다. 예측 origin은 반드시 이 값 이후여야 한다.
    train_cutoff: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(10), default="queued", index=True)  # queued|training|done|failed
    error: Mapped[str | None] = mapped_column(Text)
    state: Mapped[dict | None] = mapped_column(JSON)  # 학습 결과(파라미터)
    n_train: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    series: Mapped[Series] = relationship(lazy="joined")


class ForecastRun(Base):
    """예측 실행 1회 = (모델, origin)에서 horizon 스텝 앞까지. 한번 만들어지면 값은 수정 불가(봉인)."""
    __tablename__ = "forecast_runs"
    __table_args__ = (UniqueConstraint("model_id", "origin", name="uq_run_model_origin"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("trained_models.id", ondelete="CASCADE"), index=True)
    origin: Mapped[datetime] = mapped_column(DateTime)
    horizon: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    points: Mapped[list["Forecast"]] = relationship(
        order_by="Forecast.step", cascade="all, delete-orphan", passive_deletes=True, lazy="selectin"
    )


class Forecast(Base):
    """예측 1점. series_id/model_id는 채점·리더보드 쿼리에서 조인을 줄이려고 일부러 중복 저장(비정규화)."""
    __tablename__ = "forecasts"
    __table_args__ = (
        Index("ix_fc_series_target", "series_id", "target_ts"),
        Index(
            "ix_fc_unscored_target", "target_ts",
            sqlite_where=text("scored_at IS NULL"), postgresql_where=text("scored_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("forecast_runs.id", ondelete="CASCADE"), index=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"))
    model_id: Mapped[int] = mapped_column(ForeignKey("trained_models.id", ondelete="CASCADE"))
    step: Mapped[int] = mapped_column(Integer)  # origin으로부터 몇 스텝 뒤인지 (1..horizon)
    target_ts: Mapped[datetime] = mapped_column(DateTime)
    yhat: Mapped[float] = mapped_column(Float)
    actual: Mapped[float | None] = mapped_column(Float)
    abs_error: Mapped[float | None] = mapped_column(Float)
    sq_error: Mapped[float | None] = mapped_column(Float)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime)
