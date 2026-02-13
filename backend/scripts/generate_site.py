"""정적 사이트 생성 스크립트

장 마감 리포트 HTML을 생성하여 site/ 디렉토리에 저장.
GitHub Pages 배포용.

사용법:
    # 기본 (오늘 날짜, ../site 출력)
    uv run python -m scripts.generate_site

    # 특정 날짜
    uv run python -m scripts.generate_site --date 2026-02-13

    # 출력 디렉토리 지정
    uv run python -m scripts.generate_site --output-dir ./dist

    # 텔레그램 발송 포함
    uv run python -m scripts.generate_site --telegram

    # 데이터 수집 건너뛰기 (이미 DB에 있을 때)
    uv run python -m scripts.generate_site --skip-collect
"""

import argparse
import asyncio
import logging
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("generate_site")


@contextmanager
def _timed(label: str):
    """단계별 소요 시간 측정"""
    start = time.perf_counter()
    yield
    elapsed = time.perf_counter() - start
    logger.info("⏱  %s 소요 시간: %.1f초", label, elapsed)


def step_collect(target: date) -> None:
    """데이터 수집: 종목 마스터 + 전종목 OHLCV"""
    from screening.data.cache import CacheManager

    cache = CacheManager()

    # 종목 마스터 갱신
    stocks = cache.ensure_stock_list(target.strftime("%Y%m%d"))
    logger.info("종목 마스터: %d건", len(stocks))

    # 전종목 OHLCV 수집
    df = cache.fetch_all_ohlcv_latest()
    logger.info("전종목 OHLCV: %d건", len(df))


def step_screening(target: date) -> None:
    """스크리닝 실행"""
    from screening.data.cache import CacheManager
    from screening.engine.screener import Screener

    cache = CacheManager()
    screener = Screener(cache=cache)
    results, summary = screener.run(target)

    logger.info(
        "스크리닝 완료: 전체 %s → 최종 %s건",
        f"{summary.total_stocks:,}",
        f"{summary.final_passed:,}",
    )


def step_sector(target: date) -> None:
    """섹터 분석 + 뉴스 크롤링 + AI 요약 실행"""
    from screening.analysis.analyzer import SectorAnalyzer

    analyzer = SectorAnalyzer()
    sectors, stocks = analyzer.run(target, skip_news=False)

    logger.info(
        "섹터 분석 완료: %d개 업종, %d개 종목",
        len(sectors), len(stocks),
    )


def step_telegram() -> None:
    """최신 결과 텔레그램 발송"""
    from screening.analysis.analyzer import SectorAnalyzer
    from screening.analysis.telegram import send_daily_report
    from screening.report.page import _load_screening_results

    analyzer = SectorAnalyzer()
    sectors, stocks = analyzer.get_latest()

    if not sectors and not stocks:
        logger.warning("텔레그램: 발송할 결과 없음")
        return

    screening_results, _ = _load_screening_results(None)

    asyncio.run(send_daily_report(
        sectors, stocks, screening_results,
    ))
    logger.info("텔레그램 발송 완료")


def generate_report_html(
    target: date,
    output_dir: Path,
) -> Path:
    """리포트 HTML 생성 및 파일 저장"""
    from screening.report.page import build_report_html

    html = build_report_html(target)

    # site/{YYYY-MM-DD}/index.html
    date_dir = output_dir / target.isoformat()
    date_dir.mkdir(parents=True, exist_ok=True)

    report_path = date_dir / "index.html"
    report_path.write_text(html, encoding="utf-8")
    logger.info("리포트 저장: %s", report_path)

    return report_path


def generate_index_html(output_dir: Path) -> Path:
    """날짜 목록 index.html 생성 (최신 리다이렉트 포함)"""
    # site/ 하위 날짜 디렉토리 스캔
    date_dirs = sorted(
        [
            d.name for d in output_dir.iterdir()
            if d.is_dir() and _is_date_dir(d.name)
        ],
        reverse=True,
    )

    if not date_dirs:
        logger.warning("날짜 디렉토리 없음, index.html 생성 스킵")
        return output_dir / "index.html"

    latest = date_dirs[0]
    now = datetime.now()

    # 날짜 목록 아이템 생성
    items = []
    for d in date_dirs:
        parsed = date.fromisoformat(d)
        weekdays = "월화수목금토일"
        wd = weekdays[parsed.weekday()]
        badge = ""
        if d == latest:
            badge = (
                '<span class="ml-2 px-2 py-0.5 text-xs rounded-full'
                ' bg-emerald-500/20 text-emerald-400">최신</span>'
            )
        items.append(
            f'<a href="./{d}/" class="flex items-center justify-between'
            f' px-4 py-3 rounded-lg hover:bg-gray-800/60'
            f' transition-colors border border-gray-800/50">'
            f'<span class="flex items-center gap-2">'
            f'<span class="text-gray-300 font-medium">{d}</span>'
            f'<span class="text-gray-500 text-sm">({wd})</span>'
            f'{badge}'
            f'</span>'
            f'<span class="text-gray-600">&#8250;</span>'
            f'</a>',
        )

    items_html = "\n".join(items)

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>장 마감 리포트</title>
<script src="https://cdn.tailwindcss.com"></script>
<meta http-equiv="refresh" content="0; url=./{latest}/">
</head>
<body class="bg-gray-900 text-gray-100 min-h-screen">
<noscript>
<div class="max-w-2xl mx-auto px-4 py-8">
  <header class="mb-8">
    <h1 class="text-2xl font-bold">장 마감 리포트</h1>
    <p class="text-gray-400 mt-1">{now.strftime("%Y-%m-%d %H:%M")} 업데이트</p>
  </header>
  <div class="space-y-2">
    {items_html}
  </div>
  <footer class="mt-12 text-center text-gray-600 text-xs">
    자동 생성 by GitHub Actions
  </footer>
</div>
</noscript>
</body>
</html>"""

    index_path = output_dir / "index.html"
    index_path.write_text(html, encoding="utf-8")
    logger.info("인덱스 저장: %s (%d일치)", index_path, len(date_dirs))

    return index_path


def _is_date_dir(name: str) -> bool:
    """디렉토리 이름이 YYYY-MM-DD 형식인지 확인"""
    try:
        date.fromisoformat(name)
        return True
    except ValueError:
        return False


def main() -> None:
    from screening.database import create_db_and_tables
    create_db_and_tables()

    parser = argparse.ArgumentParser(
        description="정적 사이트 생성 (장 마감 리포트)",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="리포트 날짜 (YYYY-MM-DD, 기본: 오늘)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="../site",
        help="출력 디렉토리 (기본: ../site)",
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="텔레그램 발송 포함",
    )
    parser.add_argument(
        "--skip-collect",
        action="store_true",
        help="데이터 수집 건너뛰기 (이미 DB에 있을 때)",
    )
    args = parser.parse_args()

    target = (
        date.fromisoformat(args.date)
        if args.date else date.today()
    )
    output_dir = Path(args.output_dir).resolve()

    logger.info("=" * 50)
    logger.info("📄 정적 사이트 생성 시작")
    logger.info("  날짜: %s", target)
    logger.info("  출력: %s", output_dir)
    logger.info("=" * 50)

    total_start = time.perf_counter()

    # 1. 데이터 수집
    if not args.skip_collect:
        with _timed("데이터 수집"):
            step_collect(target)

    # 2. 스크리닝 실행
    with _timed("스크리닝"):
        step_screening(target)

    # 3. 섹터 분석
    with _timed("섹터 분석"):
        step_sector(target)

    # 4. 텔레그램 발송 (선택)
    if args.telegram:
        with _timed("텔레그램"):
            step_telegram()

    # 5. HTML 생성
    with _timed("HTML 생성"):
        generate_report_html(target, output_dir)
        generate_index_html(output_dir)

    total_elapsed = time.perf_counter() - total_start
    logger.info("=" * 50)
    logger.info(
        "✅ 정적 사이트 생성 완료: %.1f초 (%.1f분)",
        total_elapsed, total_elapsed / 60,
    )
    logger.info("  %s", output_dir / target.isoformat() / "index.html")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
