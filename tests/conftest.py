import os
import tempfile
from datetime import timedelta
from pathlib import Path

# app을 import하기 전에 테스트용 DB로 바꿔 둔다
_tmpdir = Path(tempfile.mkdtemp(prefix="hindsight-test-"))
os.environ["HINDSIGHT_DATABASE_URL"] = f"sqlite:///{(_tmpdir / 'test.db').as_posix()}"
os.environ["HINDSIGHT_EMBEDDED_SCHEDULER"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.timeutil import floor_to_grid, format_utc, utcnow  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def auth_headers(client: TestClient, email: str = "a@example.com", password: str = "password123") -> dict:
    client.post("/auth/signup", json={"email": email, "password": password})
    token = client.post("/auth/token", data={"username": email, "password": password}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth(client):
    return auth_headers(client)


def hours_ago(n: int):
    return floor_to_grid(utcnow(), "hourly") - timedelta(hours=n)


def make_series(client, headers, values, *, end_hours_ago: int = 100, name: str = "s1") -> tuple[int, list]:
    """hourly manual 시리즈를 만들고 values를 (end_hours_ago)시간 전에 끝나도록 채운다. (id, ts목록) 반환."""
    sid = client.post("/series", json={"name": name, "frequency": "hourly"}, headers=headers).json()["id"]
    n = len(values)
    ts_list = [hours_ago(end_hours_ago + n - 1 - i) for i in range(n)]
    if values:
        r = client.post(
            f"/series/{sid}/observations",
            json={"points": [{"ts": format_utc(t), "value": v} for t, v in zip(ts_list, values)]},
            headers=headers,
        )
        assert r.status_code == 200, r.text
    return sid, ts_list


def train(client, headers, sid, method="naive", params=None, cutoff=None, name=None) -> dict:
    body = {"series_id": sid, "name": name or method, "method": method, "params": params or {}}
    if cutoff is not None:
        body["train_cutoff"] = format_utc(cutoff)
    r = client.post("/models", json=body, headers=headers)
    assert r.status_code == 202, r.text
    # TestClient는 BackgroundTasks를 응답 직후 동기로 실행한다 → 여기서 조회하면 이미 끝나 있다
    return client.get(f"/models/{r.json()['id']}", headers=headers).json()
