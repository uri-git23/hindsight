from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import models  # noqa: F401  (Base.metadata에 테이블 등록)
from app.config import settings
from app.db import Base, engine
from app.errors import AppError
from app.routers import auth, forecasts, leaderboard, series
from app.routers import models as models_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    scheduler = None
    if settings.embedded_scheduler:
        from app.scheduler import start_background
        scheduler = start_background()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="hindsight",
    description="시계열을 모으고, 예측을 봉인하고, 실제값이 오면 채점해 공정한 순위를 낸다.",
    lifespan=lifespan,
)


@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(series.router)
app.include_router(leaderboard.router)
app.include_router(models_router.router)
app.include_router(forecasts.router)
