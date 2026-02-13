"""FastAPI 앱 진입점"""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from screening.database import create_db_and_tables
from screening.api.screening import router as screening_router
from screening.api.stocks import router as stocks_router
from screening.api.settings import router as settings_router
from screening.analysis.api import router as analysis_router
from screening.report.page import router as report_router
from screening.scheduler.jobs import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """앱 시작/종료 라이프사이클"""
    # 시작: DB 테이블 생성 + 스케줄러
    create_db_and_tables()
    start_scheduler()
    yield
    # 종료: 스케줄러 정리
    stop_scheduler()


app = FastAPI(
    title="주식 스크리닝 시스템",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(screening_router, prefix="/api/screening", tags=["screening"])
app.include_router(stocks_router, prefix="/api/stocks", tags=["stocks"])
app.include_router(settings_router, prefix="/api/settings", tags=["settings"])
app.include_router(
    analysis_router,
    prefix="/api/analysis/sectors",
    tags=["analysis"],
)
app.include_router(report_router, tags=["report"])


@app.get("/api/health")
def health_check() -> dict:
    """헬스 체크 엔드포인트"""
    return {"status": "ok"}
