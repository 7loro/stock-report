"""종목 API 라우터"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from screening.database import get_session
from screening.models.stock import Stock
from screening.models.ohlcv import DailyOHLCV

router = APIRouter()


@router.get("")
def get_stocks(
    market: str | None = Query(None, description="시장 구분 (KOSPI/KOSDAQ)"),
    q: str | None = Query(None, description="검색어 (종목코드 또는 종목명)"),
    session: Session = Depends(get_session),
) -> list[dict]:
    """종목 목록 조회"""
    stmt = select(Stock)

    if market:
        stmt = stmt.where(Stock.market == market.upper())

    if q:
        stmt = stmt.where(
            (Stock.ticker.contains(q)) | (Stock.name.contains(q)),
        )

    stmt = stmt.order_by(Stock.ticker)
    stocks = session.exec(stmt).all()

    return {
        "stocks": [
            {"ticker": s.ticker, "name": s.name, "market": s.market}
            for s in stocks
        ],
    }


@router.get("/{ticker}/ohlcv")
def get_ohlcv(
    ticker: str,
    days: int = Query(120, description="조회 일수"),
    session: Session = Depends(get_session),
) -> list[dict]:
    """종목 OHLCV 조회"""
    end_date = date.today()
    start_date = end_date - timedelta(days=days * 2)  # 영업일 고려 여유

    stmt = (
        select(DailyOHLCV)
        .where(DailyOHLCV.ticker == ticker)
        .where(DailyOHLCV.date >= start_date)
        .order_by(DailyOHLCV.date.desc())
        .limit(days)
    )
    rows = session.exec(stmt).all()

    return {
        "ohlcv": [
            {
                "date": r.date.isoformat(),
                "open": r.open_price,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
            }
            for r in reversed(rows)  # 오래된 순 정렬
        ],
    }
