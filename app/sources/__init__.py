from app.errors import InvalidInput
from app.sources.base import DataSource, Point
from app.sources.open_meteo import OpenMeteoSource
from app.sources.synthetic import SyntheticSource

MANUAL = "manual"  # 외부 수집 없이 POST /series/{id}/observations 로만 채우는 시리즈

REGISTRY: dict[str, DataSource] = {
    "synthetic": SyntheticSource(),
    "open_meteo": OpenMeteoSource(),
}


def get_source(name: str) -> DataSource:
    try:
        return REGISTRY[name]
    except KeyError:
        raise InvalidInput(f"unknown source '{name}'. available: {[MANUAL, *REGISTRY]}", code="unknown_source")


def validate_source(name: str, config: dict, frequency: str) -> dict:
    if name == MANUAL:
        if config:
            raise InvalidInput("manual series takes no source_config", code="invalid_source_config")
        return {}
    try:
        return get_source(name).validate_config(config, frequency)
    except ValueError as e:
        raise InvalidInput(str(e), code="invalid_source_config")


__all__ = ["DataSource", "Point", "MANUAL", "REGISTRY", "get_source", "validate_source"]
