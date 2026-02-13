"""스크리닝 API 라우터"""

import json
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from screening.database import get_session
from screening.models.screening_result import ScreeningResult
from screening.engine.screener import Screener
from screening.engine.strategy import get_strategy, list_strategies
from screening.data.cache import CacheManager

router = APIRouter()


class RunRequest(BaseModel):
    """스크리닝 실행 요청"""
    date: str | None = None  # YYYY-MM-DD 형식, 생략 시 오늘
    strategy: str | None = None  # 전략 이름, 생략 시 DEFAULT


class RunResponse(BaseModel):
    """스크리닝 실행 결과"""
    run_date: str
    total: int
    summary: dict  # 필터링 퍼널 요약
    results: list[dict]


class ResultItem(BaseModel):
    """결과 항목"""
    id: int | None
    run_date: str
    ticker: str
    name: str
    close: float
    volume: int
    passed_conditions: dict
    created_at: str


def _result_to_dict(r: ScreeningResult) -> dict:
    """ScreeningResult → dict 변환"""
    return {
        "id": r.id,
        "run_date": r.run_date.isoformat() if isinstance(r.run_date, date) else str(r.run_date),
        "ticker": r.ticker,
        "name": r.name,
        "close": r.close,
        "volume": r.volume,
        "passed_conditions": json.loads(r.passed_conditions) if r.passed_conditions else {},
        "created_at": r.created_at.isoformat() if isinstance(r.created_at, datetime) else str(r.created_at),
    }


@router.post("/run")
def run_screening(req: RunRequest) -> RunResponse:
    """스크리닝 실행"""
    if req.date:
        target_date = date.fromisoformat(req.date)
    else:
        target_date = date.today()

    strategy = get_strategy(req.strategy) if req.strategy else None
    cache = CacheManager()
    screener = Screener(strategy=strategy, cache=cache)
    results, summary = screener.run(target_date)

    return RunResponse(
        run_date=target_date.isoformat(),
        total=len(results),
        summary=summary.to_dict(),
        results=[_result_to_dict(r) for r in results],
    )


@router.get("/results")
def get_results(
    date: str = Query(..., description="날짜 (YYYY-MM-DD)", alias="date"),
    session: Session = Depends(get_session),
) -> dict:
    """특정 날짜 스크리닝 결과 조회"""
    from datetime import date as date_cls
    target = date_cls.fromisoformat(date)

    stmt = (
        select(ScreeningResult)
        .where(ScreeningResult.run_date == target)
        .order_by(ScreeningResult.volume.desc())
    )
    results = session.exec(stmt).all()
    return {
        "run_date": date,
        "total": len(results),
        "results": [_result_to_dict(r) for r in results],
    }


@router.get("/strategies")
def get_strategies() -> dict:
    """사용 가능한 스크리닝 전략 목록 조회"""
    from screening.engine.strategy import STRATEGIES

    return {
        "strategies": [
            {"name": s.name, "description": s.description}
            for s in STRATEGIES.values()
        ],
    }


@router.get("/results/latest")
def get_latest_results(
    session: Session = Depends(get_session),
) -> dict:
    """가장 최근 스크리닝 결과 조회"""
    # 최신 run_date 조회
    stmt = (
        select(ScreeningResult.run_date)
        .distinct()
        .order_by(ScreeningResult.run_date.desc())
        .limit(1)
    )
    latest_date = session.exec(stmt).first()

    if not latest_date:
        return {"run_date": None, "total": 0, "results": []}

    stmt = (
        select(ScreeningResult)
        .where(ScreeningResult.run_date == latest_date)
        .order_by(ScreeningResult.volume.desc())
    )
    results = session.exec(stmt).all()

    return {
        "run_date": latest_date.isoformat() if isinstance(latest_date, date) else str(latest_date),
        "total": len(results),
        "results": [_result_to_dict(r) for r in results],
    }
