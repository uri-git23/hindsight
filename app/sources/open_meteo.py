"""Open-Meteo (무료, API 키 불필요) 기상 데이터 소스.

실패 처리
- 타임아웃: httpx 타임아웃(설정값) → 재시도
- 5xx / 네트워크 오류: 지수 백오프로 최대 attempts번 재시도
- 4xx: 설정이 틀린 것이므로 재시도하지 않고 바로 실패
- 응답 형식이 예상과 다르면 실패 (조용히 빈 데이터로 넘어가지 않는다)
"""
import time
from datetime import datetime

import httpx

from app.config import settings
from app.errors import SourceError
from app.sources.base import Point
from app.timeutil import STEP

BASE_URL = "https://api.open-meteo.com/v1/forecast"
MAX_PAST_DAYS = 92


class OpenMeteoSource:
    name = "open_meteo"

    def __init__(self, transport: httpx.BaseTransport | None = None, attempts: int = 3, backoff: float = 0.5):
        self._transport = transport  # 테스트에서 가짜 응답(MockTransport)을 끼워 넣는 자리
        self._attempts = attempts
        self._backoff = backoff

    def validate_config(self, config: dict, frequency: str) -> dict:
        try:
            lat, lon = float(config["latitude"]), float(config["longitude"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("latitude and longitude are required numbers")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("latitude/longitude out of range")
        default_var = "temperature_2m" if frequency == "hourly" else "temperature_2m_mean"
        return {"latitude": lat, "longitude": lon, "variable": str(config.get("variable", default_var))}

    def fetch(self, config: dict, frequency: str, since: datetime | None, until: datetime) -> list[Point]:
        days_back = MAX_PAST_DAYS if since is None else (until - since).days + 1
        granularity = "hourly" if frequency == "hourly" else "daily"
        params = {
            "latitude": config["latitude"],
            "longitude": config["longitude"],
            granularity: config["variable"],
            "past_days": max(1, min(MAX_PAST_DAYS, days_back)),
            "forecast_days": 1,
            "timezone": "UTC",
        }
        body = self._get_json(params)

        try:
            block = body[granularity]
            times, values = block["time"], block[config["variable"]]
        except (KeyError, TypeError):
            raise SourceError(f"unexpected open-meteo response shape: keys={list(body)[:10]}")

        step = STEP[frequency]
        points = []
        for t, v in zip(times, values):
            if v is None:
                continue
            ts = datetime.fromisoformat(t)
            # 응답엔 '예보값'도 섞여 있다. 실제값으로 인정하는 건 구간이 끝난 것만:
            # hourly는 그 시각이 지났으면, daily는 그 날이 다 지나야 확정.
            period_end = ts if frequency == "hourly" else ts + step
            if period_end <= until:
                points.append(Point(ts, float(v)))
        return points

    def _get_json(self, params: dict) -> dict:
        last_error = "unknown"
        with httpx.Client(timeout=settings.http_timeout_seconds, transport=self._transport) as client:
            for attempt in range(self._attempts):
                try:
                    resp = client.get(BASE_URL, params=params)
                except httpx.TimeoutException:
                    last_error = f"timeout after {settings.http_timeout_seconds}s"
                except httpx.TransportError as e:
                    last_error = f"network error: {e!r}"
                else:
                    if resp.status_code < 400:
                        try:
                            return resp.json()
                        except ValueError:
                            raise SourceError("open-meteo returned non-JSON body")
                    if resp.status_code < 500:
                        raise SourceError(f"open-meteo rejected request ({resp.status_code}): {resp.text[:200]}")
                    last_error = f"open-meteo server error {resp.status_code}"

                if attempt < self._attempts - 1:
                    time.sleep(self._backoff * 2**attempt)

        raise SourceError(f"open-meteo failed after {self._attempts} attempts: {last_error}")

