"""장 마감 리포트 웹 페이지 — HTML 생성 + 라우터"""

import json
from collections import defaultdict
from datetime import date, datetime

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from screening.analysis.analyzer import SectorAnalyzer
from screening.analysis.models import SectorAnalysis, StockAnalysis
from screening.database import engine
from screening.models.screening_result import (
    ScreeningResult,
    ScreeningSummary,
)

router = APIRouter()

# ─── 포맷 헬퍼 ───────────────────────────────────────

_WEEKDAYS = "월화수목금토일"


def _fmt_date(d: date) -> str:
    """날짜를 '2026-02-12 (수)' 형태로 포맷"""
    wd = _WEEKDAYS[d.weekday()]
    return f"{d} ({wd})"


def _fmt_value(value: int | float) -> str:
    """거래대금을 조/억 단위로 포맷"""
    v = int(value)
    if v >= 1_000_000_000_000:
        return f"{v / 1_000_000_000_000:.1f}조"
    if v >= 100_000_000:
        return f"{v / 100_000_000:.0f}억"
    return f"{v:,}원"


def _fmt_price(price: int | float) -> str:
    """종가 콤마 포맷"""
    return f"{int(price):,}"


def _fmt_volume(vol: int | float) -> str:
    """거래량 축약 포맷"""
    v = int(vol)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}K"
    return f"{v:,}"


def _fmt_marcap(m: float) -> str:
    """시가총액 조/억 단위"""
    v = int(m)
    if v >= 1_000_000_000_000:
        return f"{v / 1_000_000_000_000:.1f}조"
    if v >= 100_000_000:
        return f"{v / 100_000_000:.0f}억"
    return f"{v:,}"


def _esc(text: str) -> str:
    """HTML 이스케이프"""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _badge(passed: bool) -> str:
    """통과/미통과 배지 HTML"""
    if passed:
        return '<span class="text-emerald-400">&#10003;</span>'
    return '<span class="text-red-400">&#10007;</span>'


def _colored_num(val: float, fmt: str = "+,.0f") -> str:
    """양수/음수 색상 숫자"""
    cls = "text-emerald-400" if val > 0 else "text-red-400" if val < 0 else "text-gray-400"
    return f'<span class="{cls}">{val:{fmt}}</span>'


# ─── 조건 상세 HTML 포맷터 ─────────────────────────────

def _html_condition(key: str, d: dict) -> str:
    """조건 유형별 HTML 포맷팅 분기"""
    formatters = {
        "price": _html_price,
        "volume": _html_volume,
        "trend": _html_trend,
        "golden_cross": _html_golden_cross,
        "supply_demand": _html_supply_demand,
        "financial": _html_financial,
    }
    fmt = formatters.get(key)
    if fmt:
        return fmt(d)
    # 알 수 없는 조건
    items = ", ".join(f"{k}: {v}" for k, v in d.items())
    return f'<div class="text-xs text-gray-400">[{_esc(key)}] {_esc(items)}</div>'


def _html_price(d: dict) -> str:
    """가격 조건 HTML"""
    close = d.get("close", 0)
    prev = d.get("prev_close", 0)
    opn = d.get("open", 0)
    chg = ((close - prev) / prev * 100) if prev > 0 else 0
    return (
        '<div class="space-y-0.5">'
        f'<div class="text-xs font-bold text-gray-300 mb-1">[가격] 전일 대비 상승 + 양봉</div>'
        f'<div class="text-xs">'
        f'  {_badge(d.get("P-1_종가>전일종가"))} P-1 종가 &gt; 전일종가'
        f'  <span class="text-gray-400 ml-1">{close:,.0f} &gt; {prev:,.0f} ({chg:+.2f}%)</span>'
        f'</div>'
        f'<div class="text-xs">'
        f'  {_badge(d.get("P-2_종가>시가"))} P-2 종가 &gt; 시가'
        f'  <span class="text-gray-400 ml-1">{close:,.0f} &gt; {opn:,.0f}</span>'
        f'</div>'
        '</div>'
    )


def _html_volume(d: dict) -> str:
    """거래량 조건 HTML"""
    vol = d.get("volume", 0)
    prev = d.get("prev_volume", 0)
    ma5 = d.get("volume_ma5", 0)
    ratio = (vol / prev) if prev > 0 else 0
    return (
        '<div class="space-y-0.5">'
        f'<div class="text-xs font-bold text-gray-300 mb-1">[거래량] V-1 AND (V-2 OR V-3)</div>'
        f'<div class="text-xs">'
        f'  {_badge(d.get("V-1_3만주이상"))} V-1 최소거래량'
        f'  <span class="text-gray-400 ml-1">{vol:,}주</span>'
        f'</div>'
        f'<div class="text-xs">'
        f'  {_badge(d.get("V-2_전일1.5배"))} V-2 전일대비'
        f'  <span class="text-gray-400 ml-1">{vol:,} / {prev:,} = {ratio:.1f}배</span>'
        f'</div>'
        f'<div class="text-xs">'
        f'  {_badge(d.get("V-3_5일MA돌파"))} V-3 5일MA 돌파'
        f'  <span class="text-gray-400 ml-1">{vol:,} vs MA {ma5:,.0f}</span>'
        f'</div>'
        '</div>'
    )


def _html_trend(d: dict) -> str:
    """추세 조건 HTML"""
    lines = [
        '<div class="space-y-0.5">',
        '<div class="text-xs font-bold text-gray-300 mb-1">[추세] 이평선 연속 상승</div>',
    ]
    # TREND_PERIODS 동적 추출: T_{n}일_통과 키에서 기간 파싱
    periods = sorted({
        int(k.split("_")[1].replace("일", ""))
        for k in d if k.startswith("T_") and k.endswith("_통과")
    })
    for p in periods:
        cnt = d.get(f"T_{p}일_연속상승", 0)
        ok = d.get(f"T_{p}일_통과", False)
        lines.append(
            f'<div class="text-xs">'
            f'  {_badge(ok)} {p}일선'
            f'  <span class="text-gray-400 ml-1">{cnt}일 연속 &#8593;</span>'
            f'</div>',
        )
    lines.append('</div>')
    return "\n".join(lines)


def _html_golden_cross(d: dict) -> str:
    """골든크로스 조건 HTML"""
    lines = [
        '<div class="space-y-0.5">',
        '<div class="text-xs font-bold text-gray-300 mb-1">[골든크로스] 종가 SMA 상향돌파 (1개 이상)</div>',
    ]
    periods = sorted({
        int(k.split("_")[1].replace("일", ""))
        for k in d if k.startswith("G_") and k.endswith("_통과")
    })
    for p in periods:
        ok = d.get(f"G_{p}일_통과", False)
        sma = d.get(f"G_{p}일_SMA당일", 0)
        if ok:
            lines.append(
                f'<div class="text-xs">'
                f'  {_badge(ok)} {p}일선'
                f'  <span class="text-gray-400 ml-1">SMA {sma:,.0f} 돌파</span>'
                f'</div>',
            )
        else:
            below = d.get(f"G_{p}일_전일아래", False)
            above = d.get(f"G_{p}일_당일위", False)
            reasons = []
            if not below:
                reasons.append("전일 이미 위")
            if not above:
                reasons.append("당일 미돌파")
            lines.append(
                f'<div class="text-xs">'
                f'  {_badge(ok)} {p}일선'
                f'  <span class="text-gray-500 ml-1">SMA {sma:,.0f}'
                f'  ({", ".join(reasons)})</span>'
                f'</div>',
            )
    lines.append('</div>')
    return "\n".join(lines)


def _html_supply_demand(d: dict) -> str:
    """수급 조건 HTML"""
    lines = [
        '<div class="space-y-0.5">',
        '<div class="text-xs font-bold text-gray-300 mb-1">[수급] S-1(프로그램) OR S-2(외국인+기관)</div>',
    ]
    # S-1: 프로그램 순매수
    s1 = d.get("S-1_프로그램순매수", False)
    lines.append(f'<div class="text-xs">{_badge(s1)} S-1 프로그램 순매수</div>')
    # 기간별 상세
    periods = sorted({
        int(k.split("_")[1].replace("일합계", ""))
        for k in d if k.startswith("프로그램_") and k.endswith("일합계")
    })
    if periods:
        parts = []
        for p in periods:
            val = d.get(f"프로그램_{p}일합계", 0)
            ok = d.get(f"프로그램_{p}일_순매수", False)
            parts.append(f'{_badge(ok)} {p}일 {_colored_num(val)}')
        lines.append(f'<div class="text-xs ml-4 text-gray-400">{" &middot; ".join(parts)}</div>')

    # S-2: 외국인 AND 기관
    s2 = d.get("S-2_외국인AND기관", False)
    lines.append(f'<div class="text-xs mt-1">{_badge(s2)} S-2 외국인 AND 기관</div>')
    for label in ["외국인", "기관"]:
        parts = []
        for p in periods:
            val = d.get(f"{label}_{p}일합계", 0)
            ok = d.get(f"{label}_{p}일_순매수", False)
            parts.append(f'{_badge(ok)} {p}일 {_colored_num(val)}')
        if parts:
            lines.append(
                f'<div class="text-xs ml-4 text-gray-400">{label}: {" &middot; ".join(parts)}</div>',
            )

    lines.append('</div>')
    return "\n".join(lines)


def _html_financial(d: dict) -> str:
    """실적 조건 HTML"""
    f1 = d.get("F-1_YoY증가", False)
    f1_latest = d.get("F-1_최근분기", 0)
    f1_yoy = d.get("F-1_전년동기", 0)

    f2 = d.get("F-2_QoQ증가", False)
    f2_latest = d.get("F-2_최근분기", 0)
    f2_prev = d.get("F-2_직전분기", 0)

    f3 = d.get("F-3_연간적자전환없음", False)
    f3_cur = d.get("F-3_당년영업이익", 0)
    f3_prev = d.get("F-3_전년영업이익", 0)

    f4 = d.get("F-4_분기적자전환없음", False)
    f4_cur = d.get("F-4_최근분기", 0)
    f4_prev = d.get("F-4_직전분기", 0)

    return (
        '<div class="space-y-0.5">'
        '<div class="text-xs font-bold text-gray-300 mb-1">[실적] YoY/QoQ 영업이익 증가 + 적자전환 없음</div>'
        f'<div class="text-xs">'
        f'  {_badge(f1)} F-1 YoY 증가'
        f'  <span class="text-gray-400 ml-1">{_colored_num(f1_latest)} &gt; {_colored_num(f1_yoy)}</span>'
        f'</div>'
        f'<div class="text-xs">'
        f'  {_badge(f2)} F-2 QoQ 증가'
        f'  <span class="text-gray-400 ml-1">{_colored_num(f2_latest)} &gt; {_colored_num(f2_prev)}</span>'
        f'</div>'
        f'<div class="text-xs">'
        f'  {_badge(f3)} F-3 연간 적자전환 없음'
        f'  <span class="text-gray-400 ml-1">당년 {_colored_num(f3_cur)} / 전년 {_colored_num(f3_prev)}</span>'
        f'</div>'
        f'<div class="text-xs">'
        f'  {_badge(f4)} F-4 분기 적자전환 없음'
        f'  <span class="text-gray-400 ml-1">최근 {_colored_num(f4_cur)} / 직전 {_colored_num(f4_prev)}</span>'
        f'</div>'
        '</div>'
    )


# ─── 데이터 조회 ─────────────────────────────────────

def _load_screening_results(
    target_date: date | None,
) -> tuple[list[ScreeningResult], date | None]:
    """스크리닝 결과 조회 (최신 또는 특정 날짜)"""
    try:
        return _query_screening_results(target_date)
    except Exception:
        # DB 스키마 불일치 등의 경우 빈 결과 반환
        return [], None


def _query_screening_results(
    target_date: date | None,
) -> tuple[list[ScreeningResult], date | None]:
    """스크리닝 결과 DB 쿼리"""
    with Session(engine) as session:
        if target_date:
            stmt = (
                select(ScreeningResult)
                .where(ScreeningResult.run_date == target_date)
                .order_by(ScreeningResult.volume.desc())
            )
            results = list(session.exec(stmt).all())
            for r in results:
                session.expunge(r)
            return results, target_date

        # 최신 날짜 조회
        latest = session.exec(
            select(ScreeningResult.run_date)
            .distinct()
            .order_by(ScreeningResult.run_date.desc())
            .limit(1),
        ).first()
        if not latest:
            return [], None

        stmt = (
            select(ScreeningResult)
            .where(ScreeningResult.run_date == latest)
            .order_by(ScreeningResult.volume.desc())
        )
        results = list(session.exec(stmt).all())
        for r in results:
            session.expunge(r)
        return results, latest


def _load_screening_summary(
    target_date: date | None,
) -> ScreeningSummary | None:
    """스크리닝 퍼널 요약 조회"""
    try:
        with Session(engine) as session:
            if target_date:
                stmt = select(ScreeningSummary).where(
                    ScreeningSummary.run_date == target_date,
                )
            else:
                stmt = (
                    select(ScreeningSummary)
                    .order_by(ScreeningSummary.run_date.desc())
                    .limit(1)
                )
            row = session.exec(stmt).first()
            if row:
                session.expunge(row)
            return row
    except Exception:
        return None


def _load_sector_analysis(
    target_date: date | None,
) -> tuple[list[SectorAnalysis], list[StockAnalysis], date | None]:
    """섹터/종목 분석 결과 조회"""
    analyzer = SectorAnalyzer()
    if target_date:
        sectors, stocks = analyzer.get_by_date(target_date)
        return sectors, stocks, target_date

    sectors, stocks = analyzer.get_latest()
    d = sectors[0].date if sectors else None
    return sectors, stocks, d


# ─── HTML 빌더 ────────────────────────────────────────

def _build_funnel(summary: ScreeningSummary | None) -> str:
    """스크리닝 퍼널 시각화 HTML"""
    if not summary:
        return ""

    # financial_passed가 없는 이전 DB 레코드 호환
    financial_val = getattr(summary, "financial_passed", 0)

    steps = [
        ("전체 종목", summary.total_stocks),
        ("1차 (가격+거래량)", summary.first_filter_passed),
        ("가격 조건", summary.price_passed),
        ("거래량 조건", summary.volume_passed),
        ("추세 (이평선)", summary.trend_passed),
        ("골든크로스", summary.golden_cross_passed),
        ("수급", summary.supply_demand_passed),
        ("실적", financial_val),
        ("최종 통과", summary.final_passed),
    ]

    total = summary.total_stocks or 1
    items = []
    for i, (label, count) in enumerate(steps):
        pct = count / total * 100
        # 바 너비: 최소 8%, 최대 100%
        bar_w = max(8, pct)
        # 첫 단계는 회색, 마지막은 강조, 나머지는 그라데이션
        if i == len(steps) - 1:
            bar_cls = "bg-emerald-500/80"
        elif i == 0:
            bar_cls = "bg-gray-600"
        else:
            bar_cls = "bg-gray-500/60"

        items.append(
            f'<div class="flex items-center gap-3 text-sm">'
            f'<span class="w-32 text-right text-xs text-gray-400 shrink-0">{label}</span>'
            f'<div class="flex-1 bg-gray-800 rounded-full h-5 overflow-hidden">'
            f'<div class="{bar_cls} h-full rounded-full flex items-center'
            f' justify-end pr-2 text-xs font-mono text-white/90"'
            f' style="width:{bar_w:.1f}%">'
            f'{count:,}'
            f'</div></div></div>',
        )

    funnel_body = (
        '<div class="space-y-1">'
        + "\n".join(items)
        + "</div>"
    )

    return (
        '<div class="mb-4 border border-gray-700/50 rounded-lg">'
        '<button onclick="toggleFunnel()"'
        ' class="w-full flex items-center justify-between'
        ' px-4 py-2 text-left hover:bg-gray-800/40'
        ' transition-colors rounded-lg text-sm">'
        '<span class="text-gray-400">스크리닝 퍼널</span>'
        '<span id="funnel-arrow" class="text-gray-500'
        ' transition-transform duration-200">&#9654;</span>'
        '</button>'
        f'<div id="funnel-detail" class="hidden px-4 pb-3">'
        f'{funnel_body}'
        f'</div>'
        '</div>'
    )


def _build_stock_detail(r: ScreeningResult) -> str:
    """개별 종목 조건 상세 HTML"""
    conds = r.conditions_dict
    if not conds:
        return '<div class="text-xs text-gray-500">조건 상세 데이터 없음</div>'

    sections = []
    for key, details in conds.items():
        if key == "price_preliminary" or not isinstance(details, dict):
            continue
        sections.append(_html_condition(key, details))

    if not sections:
        return '<div class="text-xs text-gray-500">조건 상세 데이터 없음</div>'

    return (
        '<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">'
        + "".join(
            f'<div class="bg-gray-800/60 rounded px-3 py-2">{s}</div>'
            for s in sections
        )
        + '</div>'
    )


def _build_screening_section(
    results: list[ScreeningResult],
    summary: ScreeningSummary | None = None,
) -> str:
    """스크리닝 종목 섹션 HTML (접기/펼치기 가능)"""
    if not results:
        return (
            '<div class="text-gray-400 text-sm py-4">'
            "스크리닝 결과 없음</div>"
        )

    # 종목 카드 목록 생성
    cards = []
    for i, r in enumerate(results):
        detail_html = _build_stock_detail(r)
        chg = getattr(r, "change_pct", 0) or 0
        sector = getattr(r, "sector", "") or ""
        # 전일종가: 종가와 등락률로 역산
        prev_close = r.close / (1 + chg / 100) if chg != 0 else r.close
        chg_cls = "text-red-400" if chg > 0 else "text-blue-400" if chg < 0 else "text-gray-400"
        cards.append(
            f'<div class="border-b border-gray-800/50">'
            # 클릭 가능한 행
            f'<button onclick="toggleStock({i})"'
            f' class="w-full flex items-center gap-3 px-3 py-2'
            f' text-left hover:bg-gray-800/40 transition-colors text-sm">'
            f'<span id="stock-arrow-{i}" class="text-gray-500 text-xs'
            f' transition-transform duration-200 shrink-0">&#9654;</span>'
            f'<span class="font-mono text-xs text-gray-500 w-14 shrink-0">{_esc(r.ticker)}</span>'
            f'<span class="font-medium w-24 shrink-0 truncate">{_esc(r.name)}</span>'
            f'<span class="text-xs text-gray-500 w-28 shrink-0 truncate">{_esc(sector)}</span>'
            f'<span class="text-right tabular-nums text-gray-400 w-20 shrink-0">{_fmt_price(prev_close)}</span>'
            f'<span class="text-right tabular-nums w-20 shrink-0">{_fmt_price(r.close)}</span>'
            f'<span class="text-right tabular-nums {chg_cls} w-16 shrink-0">{chg:+.2f}%</span>'
            f'<span class="text-right tabular-nums text-gray-400 w-16 shrink-0">{_fmt_volume(r.volume)}</span>'
            f'<span class="text-right tabular-nums text-gray-500 text-xs flex-1">{_fmt_marcap(r.market_cap)}</span>'
            f'</button>'
            # 상세 패널 (기본 숨김)
            f'<div id="stock-detail-{i}" class="hidden px-3 pb-3 pt-1">'
            f'{detail_html}'
            f'</div>'
            f'</div>',
        )

    funnel_html = _build_funnel(summary)

    # 전체 펼치기/접기 버튼
    toggle_all_btn = (
        '<div class="flex justify-end mb-2">'
        '<button onclick="toggleAllStocks()"'
        ' id="stock-toggle-all"'
        ' class="text-xs text-gray-400 hover:text-gray-200'
        ' transition-colors px-2 py-1 rounded hover:bg-gray-800/50">'
        '전체 펼치기'
        '</button>'
        '</div>'
    )

    # 헤더 행
    header = (
        '<div class="flex items-center gap-3 px-3 py-1.5 text-gray-400 text-xs border-b border-gray-700">'
        '<span class="w-3 shrink-0"></span>'
        '<span class="w-14 shrink-0">코드</span>'
        '<span class="w-24 shrink-0">종목</span>'
        '<span class="w-28 shrink-0">업종</span>'
        '<span class="text-right w-20 shrink-0">전일종가</span>'
        '<span class="text-right w-20 shrink-0">종가</span>'
        '<span class="text-right w-16 shrink-0">등락률</span>'
        '<span class="text-right w-16 shrink-0">거래량</span>'
        '<span class="text-right text-xs flex-1">시가총액</span>'
        '</div>'
    )

    stock_list = (
        '<div class="bg-gray-800/20 rounded-lg border border-gray-700/50">'
        f'{header}'
        + "\n".join(cards)
        + '</div>'
    )

    return funnel_html + toggle_all_btn + stock_list


def _build_sector_card(
    sector: SectorAnalysis,
    stocks: list[StockAnalysis],
    card_idx: int,
) -> str:
    """섹터 카드 HTML (섹터 헤더 + 종목 테이블)"""
    change_class = (
        "text-red-400" if sector.avg_change_pct > 0
        else "text-blue-400" if sector.avg_change_pct < 0
        else "text-gray-400"
    )

    # 섹터 헤더
    header = (
        '<div class="mb-3">'
        f'<div class="flex items-baseline gap-2 flex-wrap">'
        f'<span class="text-base font-bold">{_esc(sector.sector_name)}</span>'
        f'<span class="{change_class} font-bold">'
        f"{sector.avg_change_pct:+.1f}%</span>"
        "</div>"
        f'<div class="text-xs text-gray-400 mt-0.5">'
        f"상승 {sector.rising_count} / 전체 {sector.total_count}"
        f" · 거래대금 {_fmt_value(sector.total_trading_value)}"
        "</div>"
    )

    # AI 섹터 요약 (있을 때만)
    if sector.ai_summary:
        header += (
            '<div class="mt-1.5 text-sm text-yellow-300/90'
            ' bg-yellow-300/5 rounded px-2 py-1">'
            f"💡 {_esc(sector.ai_summary)}"
            "</div>"
        )
    header += "</div>"

    # 종목 테이블
    stock_rows = []
    for s in stocks:
        s_change_class = (
            "text-red-400" if s.change_pct > 0
            else "text-blue-400" if s.change_pct < 0
            else "text-gray-400"
        )

        # 뉴스 요약 셀
        news_cell = ""
        if s.ai_summary:
            src_id = f"src-{card_idx}-{s.rank}"
            source_html = ""
            if s.source_url:
                title = _esc(s.source_title or "출처")
                source_html = (
                    f'<div id="{src_id}" class="hidden mt-1 text-xs text-gray-400">'
                    f'<a href="{_esc(s.source_url)}" target="_blank"'
                    f' class="underline hover:text-gray-200">'
                    f"{title}</a></div>"
                )

            btn = ""
            if s.source_url:
                btn = (
                    f'<button onclick="toggleSrc(\'{src_id}\')"'
                    ' class="ml-1 text-gray-500 hover:text-gray-300'
                    ' transition-colors" title="출처 보기">🔗</button>'
                )

            news_cell = (
                f'<div class="flex items-start">'
                f'<span class="text-gray-300 text-xs leading-relaxed">'
                f"{_esc(s.ai_summary)}</span>"
                f"{btn}</div>{source_html}"
            )

        stock_rows.append(
            '<tr class="border-t border-gray-800/50">'
            f'<td class="py-1.5 pr-2 text-center text-xs text-gray-500">{s.rank}</td>'
            f'<td class="py-1.5 pr-3">'
            f'<div class="font-medium text-sm">{_esc(s.name)}</div>'
            f'<div class="text-xs text-gray-500 font-mono">{_esc(s.ticker)}</div>'
            "</td>"
            f'<td class="py-1.5 pr-3 text-right {s_change_class} tabular-nums text-sm">'
            f"{s.change_pct:+.1f}%</td>"
            f'<td class="py-1.5 pr-3 text-right tabular-nums text-sm">'
            f"{_fmt_price(s.close)}</td>"
            f'<td class="py-1.5 pr-3 text-right tabular-nums text-xs text-gray-400">'
            f"{_fmt_value(s.trading_value)}</td>"
            f'<td class="py-1.5 text-sm">{news_cell}</td>'
            "</tr>",
        )

    table = (
        '<table class="w-full text-sm">'
        "<thead>"
        '<tr class="text-gray-500 text-xs">'
        '<th class="text-center py-1 pr-2 w-8">#</th>'
        '<th class="text-left py-1 pr-3">종목</th>'
        '<th class="text-right py-1 pr-3">등락률</th>'
        '<th class="text-right py-1 pr-3">종가</th>'
        '<th class="text-right py-1 pr-3">거래대금</th>'
        '<th class="text-left py-1">뉴스 요약</th>'
        "</tr>"
        "</thead>"
        "<tbody>"
        + "\n".join(stock_rows)
        + "</tbody></table>"
    )

    return (
        f"{header}"
        f'<div class="overflow-x-auto">{table}</div>'
    )


def _build_collapsible_sector_card(
    sector: SectorAnalysis,
    stocks: list[StockAnalysis],
    card_idx: int,
) -> str:
    """접기/펼치기 가능한 섹터 카드"""
    change_class = (
        "text-red-400" if sector.avg_change_pct > 0
        else "text-blue-400" if sector.avg_change_pct < 0
        else "text-gray-400"
    )

    inner = _build_sector_card(sector, stocks, card_idx)

    return (
        f'<div id="sector-{card_idx}" class="bg-gray-800/40 rounded-lg'
        f' border border-gray-700/50 mb-4">'
        # 접힌 상태 헤더 (클릭으로 토글)
        f'<button onclick="toggleSector({card_idx})"'
        f' class="w-full flex items-center justify-between'
        f' px-4 py-3 text-left hover:bg-gray-700/30'
        f' transition-colors rounded-lg">'
        f'<span class="flex items-center gap-2">'
        f'<span class="text-gray-500 text-xs">{card_idx + 1}</span>'
        f'<span class="font-bold">{_esc(sector.sector_name)}</span>'
        f'<span class="{change_class} font-bold tabular-nums">'
        f'{sector.avg_change_pct:+.1f}%</span>'
        f'</span>'
        f'<span id="arrow-{card_idx}" class="text-gray-500'
        f' transition-transform duration-200">▶</span>'
        f'</button>'
        # 상세 내용 (기본 숨김)
        f'<div id="detail-{card_idx}" class="hidden px-4 pb-4">'
        f'{inner}'
        f'</div>'
        f'</div>'
    )


def _build_sector_summary_list(
    sectors: list[SectorAnalysis],
    top_n: int = 10,
) -> str:
    """상승 섹터 요약 리스트 (이름 + 등락률만)"""
    items = []
    for i, s in enumerate(sectors[:top_n]):
        change_class = (
            "text-red-400" if s.avg_change_pct > 0
            else "text-blue-400" if s.avg_change_pct < 0
            else "text-gray-400"
        )
        items.append(
            f'<button onclick="scrollToSector({i})"'
            f' class="flex items-center justify-between w-full'
            f' px-3 py-1.5 rounded hover:bg-gray-700/50'
            f' transition-colors text-sm">'
            f'<span class="flex items-center gap-2">'
            f'<span class="text-gray-500 text-xs w-5 text-right">{i + 1}</span>'
            f'<span>{_esc(s.sector_name)}</span>'
            f'</span>'
            f'<span class="{change_class} font-bold tabular-nums">'
            f'{s.avg_change_pct:+.1f}%</span>'
            f'</button>',
        )

    return (
        '<div class="bg-gray-800/40 rounded-lg border'
        ' border-gray-700/50 p-3">'
        f'<h3 class="text-sm font-bold mb-2 px-3">상승 섹터 TOP {min(len(sectors), top_n)}</h3>'
        '<div class="text-xs text-gray-400 mb-2 px-3">클릭 시 상세 내용으로 이동</div>'
        + "\n".join(items)
        + "</div>"
    )


def _build_stock_ranking_list(
    stocks: list[StockAnalysis],
    top_n: int = 10,
) -> str:
    """섹터 무관 종목 등락률 TOP N 리스트"""
    # 전체 종목에서 등락률 상위 추출 (중복 종목 제거)
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

    items = []
    for i, s in enumerate(ranked):
        change_class = (
            "text-red-400" if s.change_pct > 0
            else "text-blue-400" if s.change_pct < 0
            else "text-gray-400"
        )
        items.append(
            f'<div class="flex items-center justify-between'
            f' px-3 py-1.5 text-sm">'
            f'<span class="flex items-center gap-2">'
            f'<span class="text-gray-500 text-xs w-5 text-right">{i + 1}</span>'
            f'<span>{_esc(s.name)}</span>'
            f'<span class="text-xs text-gray-500">{_esc(s.sector_name or "")}</span>'
            f'</span>'
            f'<span class="{change_class} font-bold tabular-nums">'
            f'{s.change_pct:+.1f}%</span>'
            f'</div>',
        )

    return (
        '<div class="bg-gray-800/40 rounded-lg border'
        ' border-gray-700/50 p-3">'
        f'<h3 class="text-sm font-bold mb-2 px-3">종목 TOP {top_n}</h3>'
        '<div class="text-xs text-gray-400 mb-2 px-3">섹터 무관 등락률 순</div>'
        + "\n".join(items)
        + "</div>"
    )


def _build_sector_section(
    sectors: list[SectorAnalysis],
    stocks: list[StockAnalysis],
    top_n: int = 10,
) -> str:
    """상승 섹터 섹션 HTML (요약 리스트 + 접힌 상세 카드)"""
    if not sectors:
        return (
            '<div class="text-gray-400 text-sm py-4">'
            "섹터 분석 결과 없음</div>"
        )

    # 2열 요약: 섹터 순위 + 종목 순위
    sector_summary = _build_sector_summary_list(sectors, top_n)
    stock_ranking = _build_stock_ranking_list(stocks, top_n)
    overview = (
        '<div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">'
        f'{sector_summary}'
        f'{stock_ranking}'
        '</div>'
    )

    # 종목을 sector_code별 그룹핑
    stocks_by_sector: dict[str, list[StockAnalysis]] = (
        defaultdict(list)
    )
    for s in stocks:
        stocks_by_sector[s.sector_code].append(s)

    cards = []
    for i, sector in enumerate(sectors[:top_n]):
        sector_stocks = stocks_by_sector.get(
            sector.sector_code, [],
        )
        cards.append(
            _build_collapsible_sector_card(
                sector, sector_stocks, i,
            ),
        )

    return overview + "\n".join(cards)


def build_report_html(
    target_date: date | None = None,
) -> str:
    """장 마감 리포트 전체 HTML 생성"""
    # 데이터 조회
    screening_results, screening_date = (
        _load_screening_results(target_date)
    )
    screening_summary = _load_screening_summary(
        target_date or screening_date,
    )
    sectors, stocks, analysis_date = (
        _load_sector_analysis(target_date)
    )

    # 표시 날짜 결정
    display_date = (
        analysis_date or screening_date or date.today()
    )
    now = datetime.now()

    # 데이터 모두 없는 경우
    if not screening_results and not sectors:
        return _build_empty_page(display_date, now)

    screening_html = _build_screening_section(
        screening_results, screening_summary,
    )
    sector_html = _build_sector_section(sectors, stocks)

    return _build_page(
        display_date, now,
        screening_html, sector_html,
        len(screening_results), len(sectors),
    )


def _build_page(
    display_date: date,
    now: datetime,
    screening_html: str,
    sector_html: str,
    screening_count: int,
    sector_count: int,
) -> str:
    """전체 페이지 HTML 조립"""
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>장 마감 리포트 · {display_date}</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  .tabular-nums {{ font-variant-numeric: tabular-nums; }}
  .help-tip {{ position: relative; display: inline-flex; }}
  .help-tip .tip-body {{
    display: none; position: absolute; left: 50%; top: calc(100% + 6px);
    transform: translateX(-50%); z-index: 50; width: max-content; max-width: 340px;
    padding: 10px 14px; border-radius: 8px;
    background: #1e293b; border: 1px solid #334155; color: #cbd5e1;
    font-size: 13px; font-weight: normal; line-height: 1.5;
    white-space: normal; text-align: left; box-shadow: 0 4px 12px rgba(0,0,0,.4);
  }}
  .help-tip:hover .tip-body {{ display: block; }}
</style>
</head>
<body class="bg-gray-900 text-gray-100 min-h-screen">
<div class="max-w-5xl mx-auto px-4 py-8">

  <!-- 헤더 -->
  <header class="mb-8">
    <h1 class="text-2xl font-bold">📊 장 마감 리포트</h1>
    <p class="text-gray-400 mt-1">
      {_fmt_date(display_date)} · {now.strftime("%H:%M")} 생성
    </p>
  </header>

  <!-- 스크리닝 종목 -->
  <section class="mb-10">
    <h2 class="text-lg font-bold mb-3 pb-2 border-b border-gray-700 flex items-center gap-2">
      스크리닝 종목
      <span class="text-sm font-normal text-gray-400">{screening_count}개</span>
      <span class="help-tip">
        <span class="inline-flex items-center justify-center w-5 h-5 rounded-full
          bg-gray-700 text-gray-400 text-xs cursor-help hover:bg-gray-600
          hover:text-gray-200 transition-colors">?</span>
        <span class="tip-body">
          4단계 필터를 모두 통과한 종목입니다.<br>
          <b>1차</b> 가격 상승 + 양봉 + 최소 거래량<br>
          <b>2차</b> 이동평균선 추세(SMA) + 골든크로스<br>
          <b>3차</b> 수급 (프로그램 순매수 &gt; 0, 개인 순매도)<br>
          <b>4차</b> 실적 (YoY/QoQ 영업이익 증가 + 적자전환 없음)
        </span>
      </span>
    </h2>
    {screening_html}
  </section>

  <!-- 상승 섹터 · 종목 · 뉴스 -->
  <section>
    <h2 class="text-lg font-bold mb-3 pb-2 border-b border-gray-700 flex items-center gap-2">
      상승 섹터 · 종목 · 뉴스
      <span class="text-sm font-normal text-gray-400">상위 {min(sector_count, 10)}개</span>
      <span class="help-tip">
        <span class="inline-flex items-center justify-center w-5 h-5 rounded-full
          bg-gray-700 text-gray-400 text-xs cursor-help hover:bg-gray-600
          hover:text-gray-200 transition-colors">?</span>
        <span class="tip-body">
          당일 평균 등락률 기준 상승 섹터 상위 10개와,<br>
          각 섹터 내 등락률 상위 종목을 보여줍니다.<br>
          뉴스 요약은 Naver Finance 종목 뉴스를<br>
          AI가 요약한 결과입니다.
        </span>
      </span>
    </h2>
    {sector_html}
  </section>

</div>

<script>
function toggleSrc(id) {{
  const el = document.getElementById(id);
  if (el) el.classList.toggle('hidden');
}}
function toggleSector(idx) {{
  const detail = document.getElementById('detail-' + idx);
  const arrow = document.getElementById('arrow-' + idx);
  if (!detail) return;
  const isHidden = detail.classList.toggle('hidden');
  if (arrow) arrow.style.transform = isHidden ? '' : 'rotate(90deg)';
}}
function scrollToSector(idx) {{
  const el = document.getElementById('sector-' + idx);
  if (!el) return;
  el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
  // 자동으로 펼치기
  const detail = document.getElementById('detail-' + idx);
  const arrow = document.getElementById('arrow-' + idx);
  if (detail && detail.classList.contains('hidden')) {{
    detail.classList.remove('hidden');
    if (arrow) arrow.style.transform = 'rotate(90deg)';
  }}
}}
// 스크리닝 퍼널 토글
function toggleFunnel() {{
  const detail = document.getElementById('funnel-detail');
  const arrow = document.getElementById('funnel-arrow');
  if (!detail) return;
  const isHidden = detail.classList.toggle('hidden');
  if (arrow) arrow.style.transform = isHidden ? '' : 'rotate(90deg)';
}}
// 스크리닝 종목 상세 토글
function toggleStock(idx) {{
  const detail = document.getElementById('stock-detail-' + idx);
  const arrow = document.getElementById('stock-arrow-' + idx);
  if (!detail) return;
  const isHidden = detail.classList.toggle('hidden');
  if (arrow) arrow.style.transform = isHidden ? '' : 'rotate(90deg)';
}}
function toggleAllStocks() {{
  const btn = document.getElementById('stock-toggle-all');
  // 현재 상태 판단: 하나라도 열려있으면 '전체 접기', 아니면 '전체 펼치기'
  const details = document.querySelectorAll('[id^="stock-detail-"]');
  const arrows = document.querySelectorAll('[id^="stock-arrow-"]');
  const anyVisible = Array.from(details).some(d => !d.classList.contains('hidden'));
  details.forEach(d => {{
    if (anyVisible) d.classList.add('hidden');
    else d.classList.remove('hidden');
  }});
  arrows.forEach(a => {{
    a.style.transform = anyVisible ? '' : 'rotate(90deg)';
  }});
  if (btn) btn.textContent = anyVisible ? '전체 펼치기' : '전체 접기';
}}
</script>
</body>
</html>"""


def _build_empty_page(d: date, now: datetime) -> str:
    """데이터 없을 때 안내 페이지"""
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>장 마감 리포트</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-900 text-gray-100 min-h-screen flex items-center justify-center">
<div class="text-center">
  <h1 class="text-2xl font-bold mb-2">📊 장 마감 리포트</h1>
  <p class="text-gray-400">{_fmt_date(d)} · {now.strftime("%H:%M")} 생성</p>
  <p class="text-gray-500 mt-6">분석 결과가 없습니다.</p>
  <p class="text-gray-600 text-sm mt-1">스크리닝 또는 섹터 분석을 먼저 실행해주세요.</p>
</div>
</body>
</html>"""


# ─── FastAPI 라우터 ───────────────────────────────────

@router.get("/report", response_class=HTMLResponse)
def daily_report_page(
    date: str | None = None,
) -> HTMLResponse:
    """장 마감 리포트 웹 페이지

    Args:
        date: 분석 날짜 (YYYY-MM-DD). 생략 시 최신 데이터.
    """
    from datetime import date as date_cls

    target = None
    if date:
        try:
            target = date_cls.fromisoformat(date)
        except ValueError:
            return HTMLResponse(
                content="<h1>날짜 형식 오류: YYYY-MM-DD</h1>",
                status_code=400,
            )

    html = build_report_html(target)
    return HTMLResponse(content=html)
