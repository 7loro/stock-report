"""스크리닝 E2E 테스트 스크립트

사용법:
    # 전종목 OHLCV 히스토리 + 투자자 데이터 DB 캐싱 (첫 실행 or 갱신)
    uv run python -m scripts.test_screening --step cache

    # 스크리닝 실행 (캐시 있으면 빠름)
    uv run python -m scripts.test_screening --step run
    uv run python -m scripts.test_screening --step run --date 2026-02-13

    # 캐시 + 스크리닝 한 번에
    uv run python -m scripts.test_screening --step all
"""

import argparse
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_screening")

# OHLCV 캐싱 기간 (약 9개월 = 추세 120일 + 여유)
OHLCV_DAYS = 300
# 투자자 데이터 캐싱 기간 (약 2개월)
INVESTOR_DAYS = 60
# 병렬 워커 수
MAX_WORKERS = 16

_thread_local = threading.local()


def _get_cache():
    """스레드별 CacheManager 인스턴스"""
    from screening.data.cache import CacheManager
    if not hasattr(_thread_local, "cache"):
        _thread_local.cache = CacheManager()
    return _thread_local.cache


def _cache_stock(
    ticker: str,
    name: str,
    start: date,
    inv_start: date,
    end: date,
    progress: dict,
    total: int,
) -> None:
    """단일 종목 OHLCV + 투자자 데이터 캐싱"""
    cache = _get_cache()
    parts = []

    # OHLCV
    try:
        df = cache.ensure_ohlcv(ticker, start, end)
        parts.append(f"OHLCV {len(df)}일")
    except Exception:
        parts.append("OHLCV 실패")

    # 투자자
    try:
        df = cache.ensure_investor_data(ticker, inv_start, end)
        parts.append(f"투자자 {len(df)}일")
    except Exception:
        parts.append("투자자 실패")

    with progress["lock"]:
        progress["count"] += 1
        i = progress["count"]

    if i % 100 == 0 or i == total:
        detail = " | ".join(parts)
        logger.info(
            "  [%d/%d] %s(%s) %s",
            i, total, name, ticker, detail,
        )


def step_cache():
    """전종목 OHLCV + 투자자 데이터 DB 캐싱"""
    from screening.data.cache import CacheManager

    logger.info("=" * 50)
    logger.info("📦 전종목 DB 캐싱 시작")
    logger.info("=" * 50)

    today = date.today()
    ohlcv_start = today - timedelta(days=OHLCV_DAYS)
    inv_start = today - timedelta(days=INVESTOR_DAYS)

    # 종목 마스터
    cache = CacheManager()
    stocks = cache.ensure_stock_list(
        today.strftime("%Y%m%d"),
    )
    logger.info("종목 마스터: %d건", len(stocks))

    # 전종목 당일 OHLCV
    cache.fetch_all_ohlcv_latest()
    logger.info("전종목 당일 OHLCV 완료")

    # 캐시 갱신이 필요한 종목만 필터링 (배치 쿼리 2개)
    stale_tickers = cache.find_stale_tickers(
        [s.ticker for s in stocks], ohlcv_start, inv_start, today,
    )
    stocks_to_cache = [s for s in stocks if s.ticker in stale_tickers]

    if not stocks_to_cache:
        logger.info("✅ 모든 종목 캐시 최신 상태, 건너뜀")
        return

    total = len(stocks_to_cache)
    progress = {"count": 0, "lock": threading.Lock()}

    logger.info(
        "캐시 갱신 필요: %d/%d건 (워커 %d개)",
        total, len(stocks), MAX_WORKERS,
    )
    start_time = time.perf_counter()

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS,
    ) as executor:
        futures = [
            executor.submit(
                _cache_stock,
                s.ticker, s.name,
                ohlcv_start, inv_start, today,
                progress, total,
            )
            for s in stocks_to_cache
        ]
        for f in as_completed(futures):
            f.result()

    elapsed = time.perf_counter() - start_time
    logger.info(
        "✅ 캐싱 완료: %d건, %.1f초 (%.1f분)",
        total, elapsed, elapsed / 60,
    )


def step_run(target: date):
    """스크리닝 실행"""
    from screening.data.cache import CacheManager
    from screening.engine.screener import Screener

    logger.info("=" * 50)
    logger.info("📋 스크리닝 시작: %s", target)
    logger.info("=" * 50)

    start = time.perf_counter()
    cache = CacheManager()
    screener = Screener(cache=cache)
    results, summary = screener.run(target)
    elapsed = time.perf_counter() - start

    # 퍼널 요약
    s = summary
    logger.info("=" * 50)
    logger.info("📊 스크리닝 퍼널 요약")
    logger.info("=" * 50)
    logger.info("  전체 종목:     %s", f"{s.total_stocks:,}")
    logger.info("  1차 필터:      %s", f"{s.first_filter_passed:,}")
    logger.info("  가격 조건:     %s", f"{s.condition_passed.get('price', 0):,}")
    logger.info("  거래량 조건:   %s", f"{s.condition_passed.get('volume', 0):,}")
    logger.info("  추세 (이평선): %s", f"{s.condition_passed.get('trend', 0):,}")
    logger.info("  골든크로스:    %s", f"{s.condition_passed.get('golden_cross', 0):,}")
    logger.info("  수급:          %s", f"{s.condition_passed.get('supply_demand', 0):,}")
    logger.info("  실적:          %s", f"{s.condition_passed.get('financial', 0):,}")
    logger.info("  최종 통과:     %s", f"{s.final_passed:,}")

    # 통과 종목
    if results:
        logger.info("=" * 50)
        logger.info("✅ 통과 종목: %d개", len(results))
        logger.info("=" * 50)
        for i, r in enumerate(results, 1):
            logger.info(
                "  %2d. %s (%s) %s원 거래량 %s",
                i, r.name, r.ticker,
                f"{int(r.close):,}",
                f"{r.volume:,}",
            )
    else:
        logger.info("❌ 통과 종목 없음")

    logger.info("⏱  소요 시간: %.1f초", elapsed)


def main():
    from screening.database import create_db_and_tables
    create_db_and_tables()

    parser = argparse.ArgumentParser(
        description="스크리닝 E2E 테스트",
    )
    parser.add_argument(
        "--step",
        choices=["cache", "run", "all"],
        default="all",
        help="실행할 단계 (기본: all)",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="스크리닝 날짜 (YYYY-MM-DD, 기본: 오늘)",
    )
    args = parser.parse_args()

    target = (
        date.fromisoformat(args.date)
        if args.date else date.today()
    )

    match args.step:
        case "cache":
            step_cache()
        case "run":
            step_run(target)
        case "all":
            step_cache()
            step_run(target)


if __name__ == "__main__":
    main()
