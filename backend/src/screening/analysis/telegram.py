"""장 마감 리포트 텔레그램 알림 모듈"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from telegram import Bot

from screening.config import settings

if TYPE_CHECKING:
    from screening.analysis.models import (
        SectorAnalysis,
        StockAnalysis,
    )
    from screening.models.screening_result import (
        ScreeningResult,
    )

logger = logging.getLogger(__name__)

_WEEKDAYS = "월화수목금토일"


def _get_bot() -> Bot:
    """텔레그램 Bot 인스턴스 생성"""
    return Bot(token=settings.TELEGRAM_BOT_TOKEN)


def _format_trading_value(value: int) -> str:
    """거래대금을 읽기 좋은 형태로 포맷 (조/억 단위)"""
    if value >= 1_000_000_000_000:
        return f"{value / 1_000_000_000_000:.1f}조"
    elif value >= 100_000_000:
        return f"{value / 100_000_000:.0f}억"
    else:
        return f"{value:,}원"


def _format_price(price: int) -> str:
    """종가를 읽기 좋은 형태로 포맷"""
    return f"{price:,}원"


def _build_report_message(
    sectors: list[SectorAnalysis],
    stocks: list[StockAnalysis],
    screening_results: list[ScreeningResult] | None = None,
    top_n: int = 10,
) -> str:
    """리포트 준비 완료 알림 메시지 생성"""
    now = datetime.now()
    analysis_date = sectors[0].date if sectors else now.date()
    wd = _WEEKDAYS[analysis_date.weekday()]

    lines: list[str] = []

    # ── 헤더 ──
    lines.append(
        f"📊 장 마감 리포트 준비 완료\n"
        f"{analysis_date} ({wd}) · {now.strftime('%H:%M')} 생성",
    )
    lines.append(f"{'━' * 24}")

    # ── 스크리닝 종목 ──
    if screening_results:
        lines.append(
            f"📋 스크리닝 통과 종목: {len(screening_results)}개",
        )
        for i, r in enumerate(screening_results[:top_n], 1):
            lines.append(
                f"  {i}. {r.name} ({r.ticker})"
                f" {_format_price(int(r.close))}",
            )
        if len(screening_results) > top_n:
            lines.append(
                f"  ... 외 {len(screening_results) - top_n}개",
            )
        lines.append("")

    # ── 상승 섹터 TOP N ──
    if sectors:
        lines.append(f"🔥 상승 섹터 TOP {min(len(sectors), top_n)}")
        for i, s in enumerate(sectors[:top_n], 1):
            lines.append(
                f"  {i}. {s.sector_name}"
                f" ({s.avg_change_pct:+.1f}%)",
            )
        lines.append("")

    # ── 종목 TOP N (섹터 무관 등락률순) ──
    if stocks:
        seen: set[str] = set()
        ranked: list[StockAnalysis] = []
        sorted_stocks = sorted(
            stocks, key=lambda s: s.change_pct, reverse=True,
        )
        for s in sorted_stocks:
            if s.ticker in seen:
                continue
            seen.add(s.ticker)
            ranked.append(s)
            if len(ranked) >= top_n:
                break

        lines.append(f"📈 상승 종목 TOP {len(ranked)}")
        for i, s in enumerate(ranked, 1):
            lines.append(
                f"  {i}. {s.name}"
                f" ({s.change_pct:+.1f}%)"
                f" {_format_price(s.close)}",
            )
        lines.append("")

    # ── 웹 리포트 링크 ──
    if settings.REPORT_BASE_URL:
        url = (
            f"{settings.REPORT_BASE_URL.rstrip('/')}"
            f"/report?date={analysis_date}"
        )
        lines.append(f"🔗 상세 리포트: {url}")
    else:
        lines.append("🔗 상세 리포트: /report 페이지에서 확인")

    return "\n".join(lines)


async def send_daily_report(
    sectors: list[SectorAnalysis],
    stocks: list[StockAnalysis],
    screening_results: list[ScreeningResult] | None = None,
    top_n: int = 10,
) -> None:
    """장 마감 리포트 준비 완료 텔레그램 알림 발송

    스크리닝 종목 + 상승 섹터 TOP N + 종목 TOP N 요약과
    웹 리포트 링크를 한 메시지로 발송한다.

    Args:
        sectors: 평균 등락률 내림차순 정렬된 섹터 분석 결과
        stocks: 섹터별 상승 종목 (sector_code + rank 정렬)
        screening_results: 스크리닝 통과 종목 (없으면 생략)
        top_n: 각 섹션별 표시할 상위 N개
    """
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.warning("텔레그램 설정 누락, 알림 건너뜀")
        return

    if not sectors and not screening_results:
        return

    bot = _get_bot()
    message = _build_report_message(
        sectors, stocks, screening_results, top_n,
    )

    await bot.send_message(
        chat_id=settings.TELEGRAM_CHAT_ID,
        text=message,
    )

    logger.info(
        "장 마감 리포트 알림 텔레그램 발송 완료"
        " (스크리닝 %d, 섹터 %d, 종목 %d)",
        len(screening_results) if screening_results else 0,
        len(sectors),
        len(stocks),
    )


async def send_sector_analysis(
    results: list[SectorAnalysis],
    top_n: int = 10,
) -> None:
    """섹터 분석 결과 텔레그램 발송 (하위호환)"""
    await send_daily_report(
        sectors=results, stocks=[], top_n=top_n,
    )
