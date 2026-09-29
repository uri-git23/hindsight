"""네트워크 없이 쓸 수 있는 가짜 소스. 같은 시각이면 항상 같은 값을 준다(결정적) → 재수집해도 값이 흔들리지 않는다."""
import math
import random
from datetime import datetime

from app.sources.base import Point
from app.timeutil import STEP, floor_to_grid

DEFAULTS = {"base": 10.0, "amplitude": 5.0, "period": 24, "trend": 0.0, "noise": 0.5, "seed": 0, "backfill": 500}
MAX_POINTS_PER_FETCH = 5_000


class SyntheticSource:
    name = "synthetic"

    def validate_config(self, config: dict, frequency: str) -> dict:
        unknown = set(config) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        merged = {**DEFAULTS, **config}
        if not isinstance(merged["period"], int) or merged["period"] < 1:
            raise ValueError("period must be a positive integer")
        if not isinstance(merged["backfill"], int) or not 1 <= merged["backfill"] <= MAX_POINTS_PER_FETCH:
            raise ValueError(f"backfill must be an integer in 1..{MAX_POINTS_PER_FETCH}")
        return merged

    def fetch(self, config: dict, frequency: str, since: datetime | None, until: datetime) -> list[Point]:
        cfg = {**DEFAULTS, **config}
        step = STEP[frequency]
        end = floor_to_grid(until, frequency)
        start = since + step if since else end - step * (cfg["backfill"] - 1)

        points: list[Point] = []
        ts = start
        while ts <= end and len(points) < MAX_POINTS_PER_FETCH:
            i = int(ts.timestamp() // step.total_seconds())
            rng = random.Random(f"{cfg['seed']}:{ts.isoformat()}")
            value = (
                cfg["base"]
                + cfg["amplitude"] * math.sin(2 * math.pi * i / cfg["period"])
                + cfg["trend"] * (i % 100_000)
                + cfg["noise"] * rng.gauss(0, 1)
            )
            points.append(Point(ts, round(value, 4)))
            ts += step
        return points
