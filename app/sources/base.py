from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Point:
    ts: datetime  # UTC naive
    value: float


class DataSource(Protocol):
    """외부 데이터 소스 인터페이스. 수집 로직(services/ingest.py)은 이 두 메서드만 안다.

    새 소스 추가 = 이 프로토콜을 만족하는 클래스 작성 + sources/__init__.py의 REGISTRY에 등록.
    """

    name: str

    def validate_config(self, config: dict, frequency: str) -> dict:
        """잘못된 설정이면 ValueError."""
        ...

    def fetch(self, config: dict, frequency: str, since: datetime | None, until: datetime) -> list[Point]:
        """(since, until] 구간의 값을 돌려준다. 실패 시 app.errors.SourceError."""
        ...
