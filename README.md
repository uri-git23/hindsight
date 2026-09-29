# hindsight
Backtesting API that seals forecasts and scores them against actuals for a fair leaderboard.

시계열을 모아두고, 여러 방법으로 예측을 만들어 **봉인**한 뒤, 시간이 지나 실제값이 들어오면 채점해서
어느 방법이 잘 맞았는지 **공정하게** 순위를 내는 API.

## 실행

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

uvicorn main:app --reload       # API  → http://127.0.0.1:8000/docs
python -m app.scheduler         # 주기 작업(수집·채점·학습 줍기) — 별도 터미널
pytest                          # 테스트
python scripts/bench.py         # 30만 행 조회 성능 + 실행 계획 확인
```

개발 중 스케줄러를 API 안에서 같이 돌리려면 `HINDSIGHT_EMBEDDED_SCHEDULER=1`.

| 환경변수 | 기본값 |
|---|---|
| `HINDSIGHT_DATABASE_URL` | `sqlite:///./hindsight.db` |
| `HINDSIGHT_JWT_SECRET` | 개발용 값 — **운영에선 반드시 교체** |
| `HINDSIGHT_JWT_EXPIRE_MINUTES` | `60` |
| `HINDSIGHT_HTTP_TIMEOUT` | `10` (초, 외부 API) |
| `HINDSIGHT_EMBEDDED_SCHEDULER` | `0` |

## 한 바퀴 돌려보기 (`/docs`에서 Authorize 후)

1. `POST /auth/signup` → `POST /auth/token`
2. `POST /series` `{"name":"seoul","frequency":"hourly","source":"open_meteo","source_config":{"latitude":37.57,"longitude":126.98}}`
3. `POST /series/{id}/ingest` — 최근 92일치 수집 (네트워크 없으면 `"source":"synthetic"`)
4. `POST /models` `{"series_id":1,"name":"sn","method":"seasonal_naive","train_cutoff":"<중간 시각>"}` → **202**, `GET /models/{id}`로 status 확인
5. `POST /models/{id}/backtest` `{"start":"<cutoff>","end":"<마지막 관측>","every":6,"horizon":24}`
6. `GET /series/{id}/leaderboard` (`?step=1` 로 1스텝 예측만 비교해보기)
7. `POST /forecasts` `{"model_id":1,"horizon":24}` — 지금 시점의 라이브 예측. 시간이 지나면 스케줄러가 채점

## 구조

```
app/
  main.py            FastAPI 앱, 도메인 예외 → HTTP 변환
  routers/           HTTP만 담당 (입력 파싱, 의존성, 응답 모양)
  services/          규칙과 로직 (HTTP를 모름) ← 스케줄러도 여기만 호출
  sources/           외부 데이터 소스 (인터페이스 + 구현들)
  methods.py         예측 방법들
  jobs.py            주기 작업 함수 (FastAPI를 모름)
  scheduler.py       APScheduler로 jobs를 등록하는 별도 진입점
  deps.py            소유권 검증 의존성
  errors.py          도메인 예외 (코드 + HTTP 상태)
```

## 배우고 싶은 것 → 어디를 보면 되나

| 주제 | 파일 | 핵심 |
|---|---|---|
| 회원가입·로그인 | `security.py`, `routers/auth.py` | bcrypt 해시, JWT, OAuth2 password flow. 가입 중복은 사전 SELECT 대신 유니크 제약으로 |
| 소유권 검증 | `deps.py` | 쿼리 조건에 `owner_id`를 **함께** 건다. 남의 것은 403이 아니라 404 |
| CRUD + 상태 전이 | `services/training.py` | `TRANSITIONS` 표로 허용 전이만 통과. `queued→training`은 조건부 UPDATE로 **선점(claim)** |
| 외부 API 실패 처리 | `sources/open_meteo.py`, `services/ingest.py` | 타임아웃·5xx는 백오프 재시도, 4xx는 즉시 실패. 실패도 `IngestRun`에 기록 |
| 중복 수집 방지 | `services/history.py`, `services/ingest.py` | PK(series_id, ts) + `ON CONFLICT DO NOTHING`, 실행 중 수집 잠금, 증분 수집 |
| 소스 교체 구조 | `sources/` | `DataSource` 프로토콜 + `REGISTRY`. `PATCH /series/{id}`로 소스만 바꿔도 데이터 유지 |
| 스케줄러 분리 | `jobs.py`, `scheduler.py` | jobs는 인자 없는 평범한 함수. API를 여러 대 띄워도 스케줄러는 한 프로세스 |
| 백그라운드 작업 | `routers/models.py` | 202 + `BackgroundTasks`. 유실 대비로 스케줄러가 queued를 다시 줍고, 멈춘 training은 failed로 |
| 지연 채점 + 멱등 | `services/scoring.py` | 집합 기반 UPDATE 2단계, 모든 조건에 `scored_at IS NULL` |
| 시점 규칙 강제 | `services/forecasting.py`, `services/history.load_values` | 아래 표 참고 |
| 대용량 조회 | `models.py`, `routers/series.py`, `scripts/bench.py` | 복합 PK 인덱스, 부분 인덱스, 키셋 페이지네이션, DB 쪽 GROUP BY |
| 예외 처리 | `errors.py` | 서비스는 도메인 예외만 던지고 `{"detail","code"}`로 변환 |

## 시점 규칙 (서버가 강제)

| 시도 | 결과 |
|---|---|
| 미래 시각의 관측치 업로드 | 422 `future_observation` |
| `train_cutoff`가 지금보다 미래 | 422 `lookahead_violation` |
| 예측 origin이 지금보다 미래 | 422 `lookahead_violation` |
| **origin이 모델의 train_cutoff보다 과거** (미래 데이터로 학습된 모델 사용) | 422 `lookahead_violation` |
| 백테스트 시작이 train_cutoff보다 과거 | 422 `lookahead_violation` |
| 학습·예측 계산 | `ts <= cutoff/origin` 관측치만 읽음 (`load_values`) |

## 공정성 규칙

- 예측값은 **서버가 계산**하고 수정 API가 없다(봉인).
- 같은 (모델, origin) 예측은 한 번만 → 여러 번 뽑아 잘 나온 것만 남기기 불가 (409 `duplicate_forecast`).
- 예측 철회는 첫 목표 시각이 오기 전에만 (409 `forecast_sealed`). 예측 기록이 있는 모델은 삭제 불가.
- 관측치는 먼저 들어온 값이 이긴다 → 채점 후 정답이 바뀌지 않는다.
- 리더보드 기본값 `common_only=true`: **모든 모델이 채점받은 (target_ts, step)만** 비교. `?step=`으로 예측 거리별 비교.

## 예외 상황

| 상황 | 응답 |
|---|---|
| 없는(또는 남의) 시리즈 | 404 `series_not_found` |
| 학습 데이터 부족 | 요청 시점에 422 `insufficient_data` (방법별 최소 개수 안내) |
| 학습 중 예외 | 모델 `failed` + `error` 기록 → `POST /models/{id}/retry` |
| done이 아닌 모델로 예측 | 409 `model_not_ready` |
| 채점할 실제값이 아직 없음 | 채점 보류. 포인트 status `missing_actual`, 들어오면 다음 배치에서 채점 |
| 외부 API 타임아웃/장애 | `IngestRun.status=failed` 기록, 수동 수집 시 502 `source_error` |
| 같은 시리즈 수집 중복 실행 | 409 `ingest_in_progress` |

## 성능 (scripts/bench.py, 30만 행 × 3 시리즈, SQLite)

| 쿼리 | 시간 | 계획 |
|---|---|---|
| 1주 범위 조회 | ~2 ms | `SEARCH USING INDEX (series_id=? AND ts>? AND ts<?)` |
| 최신 시각 MAX(ts) | ~1 ms | `COVERING INDEX` |
| 1년 일별 집계 | ~10 ms | 인덱스 범위 + GROUP BY |
| 인덱스 없는 조건(value만) | ~90 ms | `SCAN observations` (반례) |

## 다음 단계 아이디어

- Alembic 마이그레이션 (지금은 `create_all`)
- PostgreSQL 전환 (upsert·집계 쿼리는 이미 dialect 분기 있음)
- 학습을 별도 워커 프로세스/큐(RQ, Celery)로
- point-in-time을 `ingested_at` 기준으로 강화 (값이 '언제 알려졌는지'까지 재현)
- 리더보드에 naive 대비 상대 성능(skill score)
