"""텔레그램 알림 모듈"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Bot

from screening.config import settings
from screening.models.screening_result import ScreeningResult

if TYPE_CHECKING:
    from screening.engine.screener import FilterSummary

logger = logging.getLogger(__name__)


def _get_bot() -> Bot:
    """텔레그램 Bot 인스턴스 생성"""
    return Bot(token=settings.TELEGRAM_BOT_TOKEN)


def format_summary_line(summary: FilterSummary) -> str:
    """퍼널 요약을 한 줄 문자열로 포맷"""
    s = summary
    return (
        f"📊 전체 {s.total_stocks:,}"
        f" → 1차 {s.first_filter_passed:,}"
        f" → 가격 {s.price_passed:,}"
        f" → 거래량 {s.volume_passed:,}"
        f" → 추세 {s.trend_passed:,}"
        f" → GC {s.golden_cross_passed:,}"
        f" → 수급 {s.supply_demand_passed:,}"
    )


def format_result(result: ScreeningResult) -> str:
    """스크리닝 결과를 텔레그램 메시지 형식으로 포맷"""
    conditions = result.conditions_dict

    # 거래량 그룹
    v = conditions.get("volume", {})
    vol_group = "A" if v.get("group_A") else "B"
    vol = v.get("volume", 0)
    prev_vol = v.get("prev_volume", 1) or 1
    vol_ratio = vol / prev_vol

    # 추세 요약
    t = conditions.get("trend", {})
    t_parts = []
    for period in [5, 20, 60, 120]:
        cnt = t.get(f"T_{period}일_연속상승", 0)
        if cnt > 0:
            t_parts.append(f"{period}일:{cnt}회")
    trend_str = ", ".join(t_parts) if t_parts else "-"

    # 수급 요약
    sd = conditions.get("supply_demand", {})
    s1 = "✅" if sd.get("S-1_프로그램순매수") else "❌"
    s2 = "✅" if sd.get("S-2_외국인AND기관") else "❌"

    return (
        f"📈 {result.name} ({result.ticker})\n"
        f"  종가: {result.close:,.0f}원 │ 거래량: {result.volume:,}주\n"
        f"  거래량: {vol_group}그룹 (x{vol_ratio:.1f})\n"
        f"  추세: {trend_str}\n"
        f"  수급: 프로그램{s1} │ 외국인+기관{s2}"
    )


async def send_screening_results(
    results: list[ScreeningResult],
    summary: FilterSummary | None = None,
) -> None:
    """스크리닝 결과 텔레그램 발송"""
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.warning("텔레그램 설정 누락, 알림 건너뜀")
        return

    if not results:
        return

    bot = _get_bot()
    run_date = results[0].run_date

    # 헤더 (퍼널 요약 포함)
    header = f"🔍 스크리닝 결과 ({run_date})\n"
    if summary is not None:
        header += format_summary_line(summary) + "\n"
    header += f"총 {len(results)}건 선정\n{'─' * 30}\n"

    body = "\n\n".join(format_result(r) for r in results)
    message = header + body

    # 텔레그램 메시지 길이 제한 (4096자)
    if len(message) > 4000:
        message = message[:4000] + "\n\n... (일부 생략)"

    await bot.send_message(
        chat_id=settings.TELEGRAM_CHAT_ID,
        text=message,
    )
    logger.info("텔레그램 알림 발송 완료: %d건", len(results))


async def send_test_message() -> None:
    """텔레그램 테스트 메시지 발송"""
    bot = _get_bot()
    await bot.send_message(
        chat_id=settings.TELEGRAM_CHAT_ID,
        text="✅ 주식 스크리닝 시스템 테스트 메시지입니다.",
    )
