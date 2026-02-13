"""섹터 분석 + 종목 분석 API 라우터"""

import json
from datetime import date, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from screening.analysis.analyzer import SectorAnalyzer
from screening.analysis.models import SectorAnalysis, StockAnalysis
from screening.analysis.sector_provider import (
    SECTOR_LIST,
    NaverSectorProvider,
)

router = APIRouter()


# --- 응답 스키마 ---

class TopGainer(BaseModel):
    """상위 상승 종목"""
    ticker: str
    name: str
    change_pct: float


class SectorResult(BaseModel):
    """업종별 분석 결과"""
    sector_code: str
    sector_name: str
    rising_count: int
    total_count: int
    avg_change_pct: float
    total_trading_value: int
    top_gainers: list[TopGainer]
    ai_summary: str | None = None
    ai_keywords: list[str] | None = None
    source_url: str | None = None
    source_title: str | None = None


class StockResult(BaseModel):
    """개별 종목 분석 결과 (섹터별 상승 종목)"""
    sector_code: str
    sector_name: str | None = None
    rank: int
    ticker: str
    name: str
    change_pct: float
    close: int
    trading_value: int
    ai_summary: str | None = None
    source_url: str | None = None
    source_title: str | None = None


class DailyReportResponse(BaseModel):
    """장 마감 통합 리포트 응답"""
    date: str
    sectors: list[SectorResult]
    stocks: list[StockResult]


class SectorAnalysisResponse(BaseModel):
    """섹터 분석 응답 (하위호환)"""
    date: str
    sectors: list[SectorResult]
    stocks: list[StockResult] = []


class SyncMappingResponse(BaseModel):
    """업종-종목 매핑 동기화 응답"""
    sectors: int
    stocks_updated: int


class SectorListItem(BaseModel):
    """업종 마스터 목록 항목"""
    sector_code: str
    sector_name: str
    stock_count: int
    updated_at: str


def _to_sector_result(sa: SectorAnalysis) -> SectorResult:
    """SectorAnalysis 모델 → API 응답 변환"""
    try:
        top_gainers = json.loads(sa.top_gainers)
    except (json.JSONDecodeError, TypeError):
        top_gainers = []

    try:
        keywords = (
            json.loads(sa.ai_keywords) if sa.ai_keywords else None
        )
    except (json.JSONDecodeError, TypeError):
        keywords = None

    return SectorResult(
        sector_code=sa.sector_code,
        sector_name=sa.sector_name,
        rising_count=sa.rising_count,
        total_count=sa.total_count,
        avg_change_pct=sa.avg_change_pct,
        total_trading_value=sa.total_trading_value,
        top_gainers=[TopGainer(**g) for g in top_gainers],
        ai_summary=sa.ai_summary,
        ai_keywords=keywords,
        source_url=sa.source_url,
        source_title=sa.source_title,
    )


def _to_stock_result(sa: StockAnalysis) -> StockResult:
    """StockAnalysis 모델 → API 응답 변환"""
    return StockResult(
        sector_code=sa.sector_code,
        sector_name=sa.sector_name,
        rank=sa.rank,
        ticker=sa.ticker,
        name=sa.name,
        change_pct=sa.change_pct,
        close=sa.close,
        trading_value=sa.trading_value,
        ai_summary=sa.ai_summary,
        source_url=sa.source_url,
        source_title=sa.source_title,
    )


# --- 엔드포인트 ---

@router.post("/run")
def run_sector_analysis(
    target_date: str | None = None,
) -> DailyReportResponse:
    """장 마감 분석 수동 실행 (섹터 + 종목 TOP 10)

    Args:
        target_date: 분석 날짜 (YYYY-MM-DD). 미지정 시 오늘.
    """
    if target_date:
        try:
            d = datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="날짜 형식 오류: YYYY-MM-DD",
            )
    else:
        d = date.today()

    analyzer = SectorAnalyzer()
    sectors, stocks = analyzer.run(d)

    return DailyReportResponse(
        date=str(d),
        sectors=[_to_sector_result(r) for r in sectors],
        stocks=[_to_stock_result(s) for s in stocks],
    )


@router.get("/daily/{target_date}")
def get_daily_analysis(
    target_date: str,
) -> DailyReportResponse:
    """특정 날짜 장 마감 분석 결과 조회"""
    try:
        d = datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="날짜 형식 오류: YYYY-MM-DD",
        )

    analyzer = SectorAnalyzer()
    sectors, stocks = analyzer.get_by_date(d)
    if not sectors and not stocks:
        raise HTTPException(
            status_code=404,
            detail=f"{target_date} 분석 결과 없음",
        )

    return DailyReportResponse(
        date=str(d),
        sectors=[_to_sector_result(r) for r in sectors],
        stocks=[_to_stock_result(s) for s in stocks],
    )


@router.get("/latest")
def get_latest_analysis() -> DailyReportResponse:
    """최신 장 마감 분석 결과 조회"""
    analyzer = SectorAnalyzer()
    sectors, stocks = analyzer.get_latest()
    if not sectors and not stocks:
        raise HTTPException(
            status_code=404, detail="분석 결과 없음",
        )

    analysis_date = (
        str(sectors[0].date) if sectors
        else str(stocks[0].date)
    )
    return DailyReportResponse(
        date=analysis_date,
        sectors=[_to_sector_result(r) for r in sectors],
        stocks=[_to_stock_result(s) for s in stocks],
    )


@router.get("/list")
def get_sector_list() -> dict:
    """업종 마스터 목록 조회 (DB 데이터 + 고정 리스트 병합)"""
    provider = NaverSectorProvider()
    db_sectors = {s.sector_code: s for s in provider.get_all_sectors()}

    items = []
    for code, name in SECTOR_LIST:
        if code in db_sectors:
            s = db_sectors[code]
            items.append(SectorListItem(
                sector_code=s.sector_code,
                sector_name=s.sector_name,
                stock_count=s.stock_count,
                updated_at=s.updated_at.isoformat(),
            ).model_dump())
        else:
            # DB에 아직 없는 업종도 고정 리스트에서 표시
            items.append(SectorListItem(
                sector_code=code,
                sector_name=name,
                stock_count=0,
                updated_at="",
            ).model_dump())

    return {"sectors": items}


@router.post("/sync-mapping")
def sync_sector_mapping() -> SyncMappingResponse:
    """업종-종목 매핑 수동 갱신 (Naver 크롤링)"""
    provider = NaverSectorProvider()
    result = provider.sync_sector_mapping()

    return SyncMappingResponse(
        sectors=result["sectors"],
        stocks_updated=result["stocks_updated"],
    )
