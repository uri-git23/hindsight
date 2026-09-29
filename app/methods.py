"""예측 방법들. 모두 같은 인터페이스를 따르므로 새 방법은 클래스 하나 추가 + METHODS 등록으로 끝난다.

- fit(y)            : 학습 구간(<= train_cutoff) 값으로 파라미터를 정한다 → state(JSON 저장 가능해야 함)
- predict(history)  : origin 시점까지의 최근 값(history_needed개)과 state로 horizon 스텝을 예측한다
"""
import math
from typing import Any

Params = dict[str, Any]


class Method:
    name: str = ""
    description: str = ""
    params_help: dict[str, str] = {}

    def default_params(self, frequency: str) -> Params:
        return {}

    def validate_params(self, params: Params, frequency: str) -> Params:
        unknown = set(params) - set(self.params_help)
        if unknown:
            raise ValueError(f"unknown params for {self.name}: {sorted(unknown)}")
        return {**self.default_params(frequency), **params}

    def min_points(self, params: Params) -> int:
        return 1

    def history_needed(self, params: Params) -> int:
        return 1

    def fit(self, y: list[float], params: Params) -> dict:
        return {}

    def predict(self, state: dict, params: Params, history: list[float], horizon: int) -> list[float]:
        raise NotImplementedError


def _positive_int(params: Params, key: str) -> int:
    v = params.get(key)
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        raise ValueError(f"'{key}' must be a positive integer")
    return v


class Naive(Method):
    name = "naive"
    description = "마지막 관측값을 그대로 반복"

    def predict(self, state, params, history, horizon):
        return [history[-1]] * horizon


class Mean(Method):
    name = "mean"
    description = "학습 구간 마지막 window개의 평균 (window 생략 시 전체)"
    params_help = {"window": "평균 낼 최근 포인트 수 (정수, 생략 시 전체)"}

    def validate_params(self, params, frequency):
        p = super().validate_params(params, frequency)
        if p.get("window") is not None:
            _positive_int(p, "window")
        return p

    def min_points(self, params):
        return params.get("window") or 1

    def fit(self, y, params):
        window = params.get("window")
        tail = y[-window:] if window else y
        return {"mean": sum(tail) / len(tail)}

    def predict(self, state, params, history, horizon):
        return [state["mean"]] * horizon


class Drift(Method):
    name = "drift"
    description = "학습 구간의 평균 기울기로 마지막 값에서 직선 연장"

    def min_points(self, params):
        return 2

    def fit(self, y, params):
        return {"slope": (y[-1] - y[0]) / (len(y) - 1)}

    def predict(self, state, params, history, horizon):
        return [history[-1] + state["slope"] * k for k in range(1, horizon + 1)]


class SeasonalNaive(Method):
    name = "seasonal_naive"
    description = "한 계절(season_length) 전 같은 위치의 값을 반복"
    params_help = {"season_length": "계절 길이 (hourly 기본 24, daily 기본 7)"}

    def default_params(self, frequency):
        return {"season_length": 24 if frequency == "hourly" else 7}

    def validate_params(self, params, frequency):
        p = super().validate_params(params, frequency)
        _positive_int(p, "season_length")
        return p

    def min_points(self, params):
        return params["season_length"]

    def history_needed(self, params):
        return params["season_length"]

    def predict(self, state, params, history, horizon):
        m = params["season_length"]
        last_season = history[-m:]
        return [last_season[(k - 1) % m] for k in range(1, horizon + 1)]


class SimpleExpSmoothing(Method):
    name = "ses"
    description = "단순 지수평활. alpha를 학습 구간의 1-step 오차가 최소가 되도록 격자 탐색"
    params_help = {}
    GRID = [round(0.05 * i, 2) for i in range(1, 20)]  # 0.05 ~ 0.95

    def min_points(self, params):
        return 10

    def history_needed(self, params):
        return 500  # 레벨은 오래전 값의 영향이 지수적으로 사라지므로 최근 500개면 충분

    @staticmethod
    def _sse(y: list[float], alpha: float) -> float:
        level, sse = y[0], 0.0
        for v in y[1:]:
            sse += (v - level) ** 2
            level = alpha * v + (1 - alpha) * level
        return sse

    def fit(self, y, params):
        best = min(self.GRID, key=lambda a: self._sse(y, a))
        return {"alpha": best, "train_rmse": math.sqrt(self._sse(y, best) / (len(y) - 1))}

    def predict(self, state, params, history, horizon):
        alpha, level = state["alpha"], history[0]
        for v in history[1:]:
            level = alpha * v + (1 - alpha) * level
        return [level] * horizon


METHODS: dict[str, Method] = {m.name: m for m in (Naive(), Mean(), Drift(), SeasonalNaive(), SimpleExpSmoothing())}
