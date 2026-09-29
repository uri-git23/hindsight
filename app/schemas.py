import math
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, PlainSerializer, field_validator

from app.timeutil import format_utc, to_utc_naive

# 입력: 어떤 timezone이든 받아서 UTC naive로 정규화 / 출력: "2026-01-01T00:00:00Z"
UtcDatetime = Annotated[datetime, AfterValidator(to_utc_naive), PlainSerializer(format_utc, return_type=str)]
Frequency = Literal["hourly", "daily"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth
class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)

    @field_validator("password")
    @classmethod
    def _bcrypt_limit(cls, v: str) -> str:
        if len(v.encode()) > 72:  # bcrypt는 72바이트까지만 본다
            raise ValueError("password must be at most 72 bytes")
        return v


class UserOut(ORM):
    id: int
    email: str
    created_at: UtcDatetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---- series
class SeriesCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    frequency: Frequency
    source: str = "manual"
    source_config: dict = Field(default_factory=dict)


class SeriesUpdate(BaseModel):
    """frequency는 바꿀 수 없다(이미 쌓인 데이터의 격자가 깨짐). 소스는 교체 가능."""
    name: str | None = Field(default=None, min_length=1, max_length=100)
    source: str | None = None
    source_config: dict | None = None


class SeriesSummary(BaseModel):
    count: int
    first_ts: UtcDatetime | None
    last_ts: UtcDatetime | None


class SeriesOut(ORM):
    id: int
    name: str
    frequency: str
    source: str
    source_config: dict
    created_at: UtcDatetime


# ---- observations
class ObservationIn(BaseModel):
    ts: UtcDatetime
    value: float

    @field_validator("value")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("value must be a finite number")
        return v


class ObservationsIn(BaseModel):
    points: list[ObservationIn] = Field(min_length=1, max_length=10_000)


class ObservationsWriteOut(BaseModel):
    received: int
    inserted: int
    duplicates: int


class ObservationOut(BaseModel):
    ts: UtcDatetime
    value: float


class ObservationPage(BaseModel):
    items: list[ObservationOut]
    next_cursor: UtcDatetime | None  # 다음 페이지는 ?after=<next_cursor>


class BucketStat(BaseModel):
    bucket: str
    count: int
    avg: float
    min: float
    max: float


class IngestRunOut(ORM):
    id: int
    source: str
    status: str
    started_at: UtcDatetime
    finished_at: UtcDatetime | None
    fetched: int
    inserted: int
    error: str | None


# ---- models
class MethodInfo(BaseModel):
    name: str
    description: str
    default_params: dict[str, dict]  # frequency -> params
    params_help: dict[str, str]


class ModelCreate(BaseModel):
    series_id: int
    name: str = Field(min_length=1, max_length=100)
    method: str
    params: dict = Field(default_factory=dict)
    train_cutoff: UtcDatetime | None = None  # 생략하면 '지금까지 들어온 마지막 관측 시각'


class ModelOut(ORM):
    id: int
    series_id: int
    name: str
    method: str
    params: dict
    train_cutoff: UtcDatetime
    status: str
    error: str | None
    state: dict | None
    n_train: int | None
    created_at: UtcDatetime
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None


class BacktestIn(BaseModel):
    start: UtcDatetime
    end: UtcDatetime
    every: int = Field(default=1, ge=1, description="origin을 몇 스텝마다 찍을지")
    horizon: int = Field(ge=1, le=500)


class BacktestOut(BaseModel):
    origins: int
    created: int
    skipped_existing: int
    scored: int


# ---- forecasts
class ForecastCreate(BaseModel):
    model_id: int
    origin: UtcDatetime | None = None  # 생략하면 '지금' 기준 마지막 관측 시각
    horizon: int = Field(ge=1, le=500)


class ForecastPointOut(BaseModel):
    step: int
    target_ts: UtcDatetime
    yhat: float
    actual: float | None
    abs_error: float | None
    # scored: 채점 완료 / awaiting_actual: 아직 목표 시각 전 / missing_actual: 시각은 지났는데 실제값이 안 들어옴
    status: Literal["scored", "awaiting_actual", "missing_actual"]


class ForecastRunOut(BaseModel):
    id: int
    series_id: int
    model_id: int
    origin: UtcDatetime
    horizon: int
    created_at: UtcDatetime
    points: list[ForecastPointOut]


class ForecastRunSummary(ORM):
    id: int
    series_id: int
    model_id: int
    origin: UtcDatetime
    horizon: int
    created_at: UtcDatetime


# ---- scoring / leaderboard
class ScoreOut(BaseModel):
    newly_scored: int
    awaiting_actual: int
    missing_actual: int


class LeaderboardRow(BaseModel):
    rank: int
    model_id: int
    model_name: str
    method: str
    n: int
    mae: float
    rmse: float
    bias: float  # 평균(yhat - actual). 양수면 과대예측 경향


class LeaderboardOut(BaseModel):
    series_id: int
    common_only: bool
    n_models: int
    awaiting_actual: int
    missing_actual: int
    rows: list[LeaderboardRow]


# ---- analytics (대시보드)
class ForecastPointRow(BaseModel):
    model_id: int
    target_ts: UtcDatetime
    yhat: float
    actual: float


class ForecastPointsOut(BaseModel):
    step: int
    truncated: bool  # 행이 너무 많아 잘렸으면 true → 기간을 좁혀서 다시 요청
    points: list[ForecastPointRow]


class ErrorTrendRow(BaseModel):
    bucket: str
    model_id: int
    n: int
    mae: float


class ErrorByStepRow(BaseModel):
    model_id: int
    step: int
    n: int
    mae: float


class LatestForecastPoint(BaseModel):
    step: int
    target_ts: UtcDatetime
    yhat: float


class LatestForecastOut(BaseModel):
    model_id: int
    run_id: int
    origin: UtcDatetime
    points: list[LatestForecastPoint]
