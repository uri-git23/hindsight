"""환경변수 기반 설정. import 시점에 한 번 읽는다."""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.getenv(f"HINDSIGHT_{name}", default)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///./hindsight.db"))
    jwt_secret: str = field(default_factory=lambda: _env("JWT_SECRET", "dev-only-secret-change-me-in-production-0000"))
    jwt_expire_minutes: int = field(default_factory=lambda: int(_env("JWT_EXPIRE_MINUTES", "60")))
    # 1이면 API 프로세스 안에서 스케줄러를 같이 돌린다 (개발 편의용). 운영에선 `python -m app.scheduler`를 따로 띄운다.
    embedded_scheduler: bool = field(default_factory=lambda: _env("EMBEDDED_SCHEDULER", "0") == "1")
    http_timeout_seconds: float = field(default_factory=lambda: float(_env("HTTP_TIMEOUT", "10")))


settings = Settings()
