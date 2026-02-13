"""섹터 분석 + 종목 TOP 10 E2E 테스트 스크립트

사용법:
    # 1단계: 업종-종목 매핑 동기화만 (최초 1회 or 월 1회)
    uv run python -m scripts.test_sector_analysis --step sync

    # 2단계: 데이터 수집 + 분석 + 텔레그램까지 전체 실행
    uv run python -m scripts.test_sector_analysis --step all

    # 통합 리포트 (종목 TOP 10 + 섹터 TOP 10)
    uv run python -m scripts.test_sector_analysis --step report

    # 개별 단계 실행
    uv run python -m scripts.test_sector_analysis --step collect   # 데이터 수집
    uv run python -m scripts.test_sector_analysis --step analyze   # 분석만 (수집 생략)
    uv run python -m scripts.test_sector_analysis --step telegram  # 최신 결과 텔레그램 발송

    # 뉴스 크롤링 + AI 요약
    uv run python -m scripts.test_sector_analysis --step news          # 뉴스 크롤링만 테스트
    uv run python -m scripts.test_sector_analysis --step analyze-news  # 분석 + 뉴스 + AI 요약
"""

import argparse
import asyncio
import logging
import sys
import time
from contextlib import contextmanager
from datetime import date

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_sector")


@contextmanager
def _timed(label: str):
    """단계별 소요 시간 측정"""
    start = time.perf_counter()
    yield
    elapsed = time.perf_counter() - start
    logger.info("⏱  %s 소요 시간: %.1f초", label, elapsed)


def step_sync():
    """업종-종목 매핑 동기화 (Naver 크롤링)"""
    logger.info("=" * 50)
    logger.info("📋 업종-종목 매핑 동기화 시작")
    logger.info("=" * 50)

    from screening.analysis.sector_provider import NaverSectorProvider

    provider = NaverSectorProvider()
    result = provider.sync_sector_mapping()

    logger.info(
        "✅ 완료: %d개 업종, %d개 종목 매핑",
        result["sectors"], result["stocks_updated"],
    )
    return result


def step_collect():
    """전종목 데이터 수집"""
    logger.info("=" * 50)
    logger.info("📥 전종목 데이터 수집 시작")
    logger.info("=" * 50)

    from screening.data.cache import CacheManager

    today = date.today()
    cache = CacheManager()

    # 종목 마스터 갱신
    stocks = cache.ensure_stock_list(today.strftime("%Y%m%d"))
    logger.info("종목 마스터: %d건", len(stocks))

    # 전종목 OHLCV 확인
    df = cache.fetch_all_ohlcv_latest()
    logger.info("전종목 OHLCV: %d건", len(df))
    logger.info("✅ 데이터 수집 완료")
    return df


def step_analyze():
    """섹터 분석 실행 (섹터별 상승 종목)"""
    logger.info("=" * 50)
    logger.info("📊 장 마감 분석 시작")
    logger.info("=" * 50)

    from collections import defaultdict

    from screening.analysis.analyzer import SectorAnalyzer

    today = date.today()
    analyzer = SectorAnalyzer()
    sectors, stocks = analyzer.run(today)

    if not sectors and not stocks:
        logger.warning(
            "❌ 분석 결과 없음 (매핑 먼저 실행: --step sync)",
        )
        return [], []

    # 종목을 sector_code별 그룹핑
    stocks_by_sector = defaultdict(list)
    for s in stocks:
        stocks_by_sector[s.sector_code].append(s)

    # 상위 섹터 + 소속 종목 출력
    if sectors:
        logger.info("=" * 50)
        logger.info("🔥 상승 섹터 TOP 10 + 소속 상승 종목")
        logger.info("=" * 50)

        for i, r in enumerate(sectors[:10], 1):
            logger.info(
                "%2d. %-20s %+6.2f%% "
                "(상승 %d/%d, 거래대금 %s)",
                i, r.sector_name, r.avg_change_pct,
                r.rising_count, r.total_count,
                _fmt_value(r.total_trading_value),
            )

            sector_stocks = stocks_by_sector.get(
                r.sector_code, [],
            )
            for s in sector_stocks:
                logger.info(
                    "    %2d. %s (%s) %+6.2f%%"
                    " 종가 %s 거래대금 %s",
                    s.rank, s.name, s.ticker,
                    s.change_pct,
                    _fmt_price(s.close),
                    _fmt_value(s.trading_value),
                )

    logger.info(
        "✅ 분석 완료: %d개 업종, %d개 종목",
        len(sectors), len(stocks),
    )
    return sectors, stocks


def step_telegram(sectors=None, stocks=None):
    """텔레그램 발송"""
    logger.info("=" * 50)
    logger.info("📨 텔레그램 발송 시작")
    logger.info("=" * 50)

    from screening.analysis.analyzer import SectorAnalyzer
    from screening.analysis.telegram import send_daily_report
    from screening.report.page import _load_screening_results

    if sectors is None or stocks is None:
        # DB에서 최신 결과 로드
        analyzer = SectorAnalyzer()
        sectors, stocks = analyzer.get_latest()

    if not sectors and not stocks:
        logger.warning("❌ 발송할 결과 없음")
        return

    # 스크리닝 결과도 함께 로드
    screening_results, _ = _load_screening_results(None)

    asyncio.run(send_daily_report(
        sectors, stocks, screening_results,
    ))
    logger.info("✅ 텔레그램 발송 완료")


def step_news():
    """뉴스 크롤링만 테스트 (상위 종목 3개)"""
    logger.info("=" * 50)
    logger.info("📰 뉴스 크롤링 테스트")
    logger.info("=" * 50)

    from screening.analysis.news_provider import (
        NaverStockNewsProvider,
    )

    provider = NaverStockNewsProvider()

    # 대표 종목 3개 테스트
    test_tickers = ["005930", "000660", "035420"]
    test_names = {"005930": "삼성전자", "000660": "SK하이닉스", "035420": "NAVER"}

    for ticker in test_tickers:
        name = test_names.get(ticker, ticker)
        news = provider.fetch_stock_news(ticker, max_items=3)
        logger.info("📰 %s (%s): %d건", name, ticker, len(news))
        for n in news:
            logger.info(
                "    [%s] %s (%s)", n.source, n.title, n.date,
            )
            logger.info("    URL: %s", n.url)

    logger.info("✅ 뉴스 크롤링 테스트 완료")


def step_analyze_news():
    """분석 + 뉴스 크롤링 + AI 요약 전체 테스트"""
    logger.info("=" * 50)
    logger.info("📊 분석 + 뉴스 + AI 요약 시작")
    logger.info("=" * 50)

    from collections import defaultdict

    from screening.analysis.analyzer import SectorAnalyzer
    from screening.analysis.news_provider import (
        NaverStockNewsProvider,
    )
    from screening.config import settings

    today = date.today()
    analyzer = SectorAnalyzer()

    # ① 분석 (뉴스 없이, 섹터/종목 데이터만)
    sectors, stocks = analyzer.run(today)

    if not sectors and not stocks:
        logger.warning(
            "❌ 분석 결과 없음 (매핑 먼저 실행: --step sync)",
        )
        return [], []

    # 종목을 sector_code별 그룹핑
    stocks_by_sector = defaultdict(list)
    for s in stocks:
        stocks_by_sector[s.sector_code].append(s)

    # ② 뉴스 크롤링 + 링크 출력
    all_tickers = [s.ticker for s in stocks]
    news_provider = NaverStockNewsProvider(
        delay=settings.NEWS_CRAWL_DELAY,
    )
    logger.info("=" * 50)
    logger.info("📰 뉴스 크롤링: %d개 종목", len(all_tickers))
    logger.info("=" * 50)

    all_news = news_provider.fetch_bulk_news(
        all_tickers,
        max_per_stock=settings.NEWS_PER_STOCK,
    )

    # 섹터별 종목 뉴스 링크 출력
    for i, r in enumerate(sectors[:10], 1):
        logger.info(
            "%2d. %-20s %+6.2f%%",
            i, r.sector_name, r.avg_change_pct,
        )
        sector_stocks = stocks_by_sector.get(
            r.sector_code, [],
        )
        for s in sector_stocks:
            news_items = all_news.get(s.ticker, [])
            logger.info(
                "    %s (%s) %+.1f%% — 뉴스 %d건",
                s.name, s.ticker, s.change_pct,
                len(news_items),
            )
            for n in news_items:
                logger.info(
                    "      [%s] %s", n.source, n.title,
                )
                logger.info("        %s", n.url)

    # ③ AI 요약 (API 키 있을 때만)
    if not settings.AI_API_KEY:
        logger.info("AI API 키 미설정, AI 요약 건너뜀")
    else:
        logger.info("=" * 50)
        logger.info("🤖 AI 뉴스 요약 시작")
        logger.info("=" * 50)
        # 뉴스 포함 재분석
        sectors, stocks = analyzer.run(
            today, skip_news=False,
        )
        # 재그룹핑
        stocks_by_sector = defaultdict(list)
        for s in stocks:
            stocks_by_sector[s.sector_code].append(s)

    # ④ 최종 결과 출력
    if sectors:
        logger.info("=" * 50)
        logger.info("🔥 상승 섹터 TOP 10 + 뉴스 요약")
        logger.info("=" * 50)

        for i, r in enumerate(sectors[:10], 1):
            logger.info(
                "%2d. %-20s %+6.2f%%",
                i, r.sector_name, r.avg_change_pct,
            )
            if r.ai_summary:
                logger.info("    💡 %s", r.ai_summary)

            sector_stocks = stocks_by_sector.get(
                r.sector_code, [],
            )
            for s in sector_stocks:
                summary = (
                    f" → {s.ai_summary}"
                    if s.ai_summary else ""
                )
                logger.info(
                    "    %2d. %s (%s) %+6.2f%%%s",
                    s.rank, s.name, s.ticker,
                    s.change_pct, summary,
                )

    logger.info(
        "✅ 분석+뉴스 완료: %d개 업종, %d개 종목",
        len(sectors), len(stocks),
    )
    return sectors, stocks


def step_all():
    """전체 파이프라인: 수집 → 분석 → 텔레그램"""
    with _timed("수집"):
        step_collect()
    with _timed("분석"):
        sectors, stocks = step_analyze()
    if sectors or stocks:
        with _timed("텔레그램"):
            step_telegram(sectors, stocks)


def step_report():
    """통합 리포트: 수집 → 분석(종목+섹터) → 텔레그램"""
    with _timed("수집"):
        step_collect()
    with _timed("분석"):
        sectors, stocks = step_analyze()
    if sectors or stocks:
        with _timed("텔레그램"):
            step_telegram(sectors, stocks)


def _fmt_value(value: int) -> str:
    """거래대금 포맷"""
    if value >= 1_000_000_000_000:
        return f"{value / 1_000_000_000_000:.1f}조"
    elif value >= 100_000_000:
        return f"{value / 100_000_000:.0f}억"
    return f"{value:,}"


def _fmt_price(price: int) -> str:
    """종가 포맷"""
    return f"{price:,}원"


def main():
    from screening.database import create_db_and_tables
    create_db_and_tables()

    parser = argparse.ArgumentParser(
        description="장 마감 분석 E2E 테스트",
    )
    parser.add_argument(
        "--step",
        choices=[
            "sync", "collect", "analyze",
            "telegram", "report", "all",
            "news", "analyze-news",
        ],
        default="all",
        help="실행할 단계 (기본: all)",
    )
    args = parser.parse_args()

    with _timed(f"전체 ({args.step})"):
        match args.step:
            case "sync":
                step_sync()
            case "collect":
                step_collect()
            case "analyze":
                step_analyze()
            case "telegram":
                step_telegram()
            case "report":
                step_report()
            case "all":
                step_all()
            case "news":
                step_news()
            case "analyze-news":
                with _timed("수집"):
                    step_collect()
                with _timed("분석+뉴스"):
                    sectors, stocks = step_analyze_news()
                if sectors or stocks:
                    with _timed("텔레그램"):
                        step_telegram(sectors, stocks)


if __name__ == "__main__":
    main()
