"""시간 규칙: DB에는 전부 'UTC naive'로 저장하고, API 경계에서만 변환한다."""
from datetime import datetime, timedelta, timezone

STEP = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc_naive(dt: datetime) -> datetime:
    """timezone이 붙어 있으면 UTC로 바꾸고 떼어낸다. naive면 이미 UTC라고 본다."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def format_utc(dt: datetime) -> str:
    return to_utc_naive(dt).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def floor_to_grid(dt: datetime, frequency: str) -> datetime:
    dt = dt.replace(minute=0, second=0, microsecond=0)
    if frequency == "daily":
        dt = dt.replace(hour=0)
    return dt


def is_on_grid(dt: datetime, frequency: str) -> bool:
    return floor_to_grid(dt, frequency) == dt
