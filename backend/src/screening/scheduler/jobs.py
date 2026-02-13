"""APScheduler 기반 스케줄 작업"""

import logging
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from screening.data.cache import CacheManager
from screening.engine.screener import Screener
from screening.engine.strategy import DEFAULT

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def collect_data_job() -> None:
    """평일 18:00 데이터 수집 작업"""
    logger.info("=== 데이터 수집 작업 시작 ===")
    try:
        today = date.today()
        cache = CacheManager()

        # 종목 마스터 갱신
        cache.ensure_stock_list(today.strftime("%Y%m%d"))

        # 당일 전종목 OHLCV 수집
        cache.ensure_all_ohlcv(today)

        logger.info("데이터 수집 완료: %s", today)
    except Exception:
        logger.exception("데이터 수집 실패")


def run_screening_job() -> None:
    """평일 18:30 스크리닝 실행 + 알림"""
    logger.info("=== 스크리닝 작업 시작 ===")
    try:
        today = date.today()
        cache = CacheManager()
        screener = Screener(strategy=DEFAULT, cache=cache)
        results, summary = screener.run(today)

        logger.info("스크리닝 완료: %d건", len(results))

        # 결과가 있으면 텔레그램 알림
        if results:
            try:
                import asyncio
                from screening.notification.telegram import send_screening_results
                asyncio.run(send_screening_results(results, summary))
            except Exception:
                logger.exception("텔레그램 알림 발송 실패")

    except Exception:
        logger.exception("스크리닝 실패")


def analyze_sectors_job() -> None:
    """평일 18:45 장 마감 분석 (섹터 + 종목 TOP 10) + 알림"""
    logger.info("=== 장 마감 분석 작업 시작 ===")
    try:
        from screening.analysis.analyzer import SectorAnalyzer

        today = date.today()
        analyzer = SectorAnalyzer()
        sectors, stocks = analyzer.run(today)

        logger.info(
            "장 마감 분석 완료: %d개 업종, %d개 종목",
            len(sectors), len(stocks),
        )

        # 결과가 있으면 텔레그램 알림
        if sectors or stocks:
            try:
                import asyncio
                from screening.analysis.telegram import (
                    send_daily_report,
                )
                from screening.report.page import (
                    _load_screening_results,
                )
                screening_results, _ = _load_screening_results(today)
                asyncio.run(send_daily_report(
                    sectors, stocks, screening_results,
                ))
            except Exception:
                logger.exception("장 마감 리포트 텔레그램 발송 실패")

    except Exception:
        logger.exception("장 마감 분석 실패")


def start_scheduler() -> None:
    """스케줄러 시작"""
    # 평일 18:00 데이터 수집
    scheduler.add_job(
        collect_data_job,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=18,
            minute=0,
        ),
        id="collect_data",
        name="데이터 수집",
        replace_existing=True,
    )

    # 평일 18:30 스크리닝 실행
    scheduler.add_job(
        run_screening_job,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=18,
            minute=30,
        ),
        id="run_screening",
        name="스크리닝 실행",
        replace_existing=True,
    )

    # 평일 18:45 섹터 분석
    scheduler.add_job(
        analyze_sectors_job,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=18,
            minute=45,
        ),
        id="analyze_sectors",
        name="섹터 분석",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("스케줄러 시작 완료")


def stop_scheduler() -> None:
    """스케줄러 중지"""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("스케줄러 중지 완료")
