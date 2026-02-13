"""메인 스크리너 - 1차→2차→3차→4차 단계별 필터링

1차 필터: FDR StockListing → 가격(P-1,P-2) + 최소 거래량 (전종목 벡터 연산)
2차 필터: FDR 개별 종목 OHLCV → 거래량 정밀 + 추세 + 골든크로스
3차 필터: Naver 투자자 데이터 → 수급
4차 필터: Naver Finance 재무제표 → 실적/컨센서스
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd
from sqlmodel import Session

from screening.config import settings
from screening.database import engine
from screening.data.cache import CacheManager
from screening.models.screening_result import (
    ScreeningResult,
    ScreeningSummary,
)

logger = logging.getLogger(__name__)

# 단계별 종목 추적 최대 개수 (로깅용)
_MAX_STAGE_TRACK = 10


@dataclass
class FilterSummary:
    """필터링 퍼널 요약 통계"""

    total_stocks: int = 0
    first_filter_passed: int = 0
    # 조건별 통과 수 (동적)
    condition_passed: dict[str, int] = field(default_factory=dict)
    final_passed: int = 0
    strategy_name: str = ""

    # 단계별 통과 종목 (로깅용, to_dict 미포함)
    stage_stocks: dict[str, list[str]] = field(default_factory=dict)
    # 단계별 소요시간 (초)
    stage_elapsed: dict[str, float] = field(default_factory=dict)

    def track(self, stage: str, name: str, ticker: str) -> None:
        """종목 추적 (10개 미만 표시용)"""
        stocks = self.stage_stocks.setdefault(stage, [])
        if len(stocks) < _MAX_STAGE_TRACK:
            stocks.append(f"{name}({ticker})")

    def to_dict(self) -> dict:
        result = {
            "total_stocks": self.total_stocks,
            "first_filter_passed": self.first_filter_passed,
            "final_passed": self.final_passed,
            "strategy_name": self.strategy_name,
        }
        result.update(
            {f"{k}_passed": v for k, v in self.condition_passed.items()},
        )
        return result


class Screener:
    """4단계 스크리닝 엔진"""

    def __init__(
        self,
        strategy: "ScreeningStrategy | None" = None,
        cache: CacheManager | None = None,
    ) -> None:
        from screening.engine.strategy import DEFAULT, ScreeningStrategy  # noqa: F811

        self._strategy: ScreeningStrategy = strategy or DEFAULT
        self._cache = cache or CacheManager()

    def run(
        self,
        target_date: date,
    ) -> tuple[list[ScreeningResult], FilterSummary]:
        """전체 스크리닝 실행. (결과 리스트, 퍼널 요약) 반환"""
        t_total = time.perf_counter()
        logger.info(
            "=== 스크리닝 시작: %s (전략: %s) ===",
            target_date,
            self._strategy.name,
        )
        summary = FilterSummary(strategy_name=self._strategy.name)

        # 종목 마스터 갱신 + 종목명 매핑
        t0 = time.perf_counter()
        stocks = self._cache.ensure_stock_list(target_date.strftime("%Y%m%d"))
        stock_map = {s.ticker: s.name for s in stocks}
        sector_map = {s.ticker: (s.sector_name or "") for s in stocks}
        summary.stage_elapsed["종목마스터"] = time.perf_counter() - t0

        # 1차 필터: StockListing → 가격 + 최소 거래량 (벡터 연산)
        t0 = time.perf_counter()
        candidates = self._first_filter(stock_map, summary, sector_map)
        summary.stage_elapsed["1차 필터"] = time.perf_counter() - t0
        logger.info(
            "1차 필터 통과: %d / %d건",
            summary.first_filter_passed,
            summary.total_stocks,
        )

        if not candidates:
            summary.stage_elapsed["전체"] = time.perf_counter() - t_total
            self._log_summary(summary)
            return [], summary

        # 2차 필터: 개별 OHLCV → technical_conditions
        t0 = time.perf_counter()
        candidates = self._second_filter(target_date, candidates, summary)
        summary.stage_elapsed["2차 필터"] = time.perf_counter() - t0
        if self._strategy.technical_conditions:
            last_cond = self._strategy.technical_conditions[-1].name
            last_count = summary.condition_passed.get(last_cond, 0)
            logger.info("2차 필터 통과: %d건", last_count)

        if not candidates:
            summary.stage_elapsed["전체"] = time.perf_counter() - t_total
            self._log_summary(summary)
            return [], summary

        # 3차 필터: supply_conditions (비어있으면 스킵)
        t0 = time.perf_counter()
        candidates = self._third_filter(target_date, candidates, summary)
        summary.stage_elapsed["3차 필터"] = time.perf_counter() - t0
        if self._strategy.supply_conditions:
            last_cond = self._strategy.supply_conditions[-1].name
            last_count = summary.condition_passed.get(last_cond, 0)
            logger.info("3차 필터 통과: %d건", last_count)

        if not candidates:
            summary.stage_elapsed["전체"] = time.perf_counter() - t_total
            self._log_summary(summary)
            return [], summary

        # 4차 필터: financial_conditions (비어있으면 스킵)
        t0 = time.perf_counter()
        results = self._fourth_filter(target_date, candidates, summary)
        summary.stage_elapsed["4차 필터"] = time.perf_counter() - t0
        logger.info("최종 통과: %d건", summary.final_passed)

        summary.stage_elapsed["전체"] = time.perf_counter() - t_total

        # 퍼널 요약 + 최종 종목 상세 로깅
        self._log_summary(summary)
        self._log_final_results(results)

        # DB 저장
        self._save_results(results)
        self._save_summary(target_date, summary)

        return results, summary

    def _first_filter(
        self,
        stock_map: dict[str, str],
        summary: FilterSummary | None = None,
        sector_map: dict[str, str] | None = None,
    ) -> list[dict]:
        """1차 필터: StockListing → P-1(상승) + P-2(양봉) + 최소 거래량

        pandas boolean masking으로 전종목 일괄 필터링 후, 통과 종목만 dict 생성.
        """
        today_df = self._cache.fetch_all_ohlcv_latest()
        if today_df.empty:
            logger.warning("전 종목 OHLCV 데이터 없음")
            return []

        # NaN 행 제거 (FDR StockListing에 간혹 NaN 포함)
        today_df = today_df.dropna(subset=["close", "open", "volume"])

        if summary is not None:
            summary.total_stocks = len(today_df)

        # pandas 벡터 연산으로 조건 필터링
        mask = (
            (today_df["close"] > 0)
            & (today_df["open"] > 0)
            & (today_df["close"] > today_df["open"])   # P-2: 양봉
            & (today_df["volume"] >= settings.VOLUME_MIN)  # 최소 거래량
        )
        if "changes" in today_df.columns:
            mask = mask & (today_df["changes"] > 0)  # P-1: 상승

        filtered = today_df[mask]
        has_marcap = "marcap" in today_df.columns

        # 통과 종목만 dict 생성
        candidates = []
        for ticker in filtered.index:
            ticker_str = str(ticker)
            row = filtered.loc[ticker]
            name = stock_map.get(ticker_str, ticker_str)

            close_val = float(row["close"])
            open_val = float(row["open"])
            # change_pct는 FDR에서 이미 퍼센트(%)로 제공됨
            chg_pct = float(row["change_pct"]) if "change_pct" in filtered.columns else 0

            candidates.append({
                "ticker": ticker_str,
                "name": name,
                "close": close_val,
                "open": open_val,
                "volume": int(row["volume"]),
                "market_cap": float(row["marcap"]) if has_marcap else 0,
                "change_pct": round(chg_pct, 2),
                "sector": (sector_map or {}).get(ticker_str, ""),
                "conditions": {
                    "price_preliminary": {"P-1_상승": True, "P-2_양봉": True},
                },
            })

            if summary is not None:
                summary.track("first_filter", name, ticker_str)

        if summary is not None:
            summary.first_filter_passed = len(candidates)

        return candidates

    def _second_filter(
        self,
        target_date: date,
        candidates: list[dict],
        summary: FilterSummary | None = None,
    ) -> list[dict]:
        """2차 필터: 개별 OHLCV → technical_conditions 순회 (AND 평가)"""
        max_period = max(settings.TREND_PERIODS) + settings.TREND_MIN_COUNT + 10
        start_date = target_date - timedelta(days=max_period * 2)

        passed = []
        for cand in candidates:
            ticker = cand["ticker"]
            name = cand["name"]

            # 개별 종목 OHLCV 캐시 로드 (FDR DataReader)
            ohlcv_df = self._cache.ensure_ohlcv(ticker, start_date, target_date)
            if ohlcv_df.empty or len(ohlcv_df) < 12:
                continue

            # 전략의 technical_conditions 순회 (AND)
            all_passed = True
            for condition in self._strategy.technical_conditions:
                result = condition.evaluate(ticker, ohlcv_df)
                if not result.passed:
                    all_passed = False
                    break
                if summary is not None:
                    cnt = summary.condition_passed.get(condition.name, 0)
                    summary.condition_passed[condition.name] = cnt + 1
                    summary.track(condition.name, name, ticker)
                cand["conditions"][condition.name] = result.details

            if all_passed:
                passed.append(cand)

        return passed

    def _third_filter(
        self,
        target_date: date,
        candidates: list[dict],
        summary: FilterSummary | None = None,
    ) -> list[dict]:
        """3차 필터: supply_conditions 순회 (비어있으면 스킵)"""
        # supply_conditions가 없으면 2차 통과 결과를 그대로 반환
        if not self._strategy.supply_conditions:
            return candidates

        max_sell_period = max(settings.SUPPLY_DEMAND_PERIODS)
        start_date = target_date - timedelta(days=max_sell_period * 2)

        passed = []
        for cand in candidates:
            ticker = cand["ticker"]

            investor_df = self._cache.ensure_investor_data(
                ticker, start_date, target_date,
            )

            # supply_conditions 순회 (AND)
            all_passed = True
            for condition in self._strategy.supply_conditions:
                sd_result = condition.evaluate(
                    ticker,
                    pd.DataFrame(),
                    investor_df=investor_df,
                )
                if not sd_result.passed:
                    all_passed = False
                    break
                if summary is not None:
                    cnt = summary.condition_passed.get(condition.name, 0)
                    summary.condition_passed[condition.name] = cnt + 1
                    summary.track(condition.name, cand["name"], ticker)
                cand["conditions"][condition.name] = sd_result.details

            if all_passed:
                passed.append(cand)

        return passed

    def _fourth_filter(
        self,
        target_date: date,
        candidates: list[dict],
        summary: FilterSummary | None = None,
    ) -> list[ScreeningResult]:
        """4차 필터: financial_conditions 순회 (비어있으면 스킵)"""
        # financial_conditions가 없으면 바로 최종 결과로 변환
        if not self._strategy.financial_conditions:
            results = [
                self._build_result(target_date, cand) for cand in candidates
            ]
            if summary is not None:
                summary.final_passed = len(results)
            return results

        results = []
        for cand in candidates:
            ticker = cand["ticker"]

            # 재무제표 캐시 조회/크롤링
            quarterly_df, annual_df = self._cache.ensure_financial_data(ticker)

            # financial_conditions 순회 (AND)
            all_passed = True
            for condition in self._strategy.financial_conditions:
                fin_result = condition.evaluate(
                    ticker,
                    pd.DataFrame(),
                    quarterly_df=quarterly_df,
                    annual_df=annual_df,
                )
                if not fin_result.passed:
                    all_passed = False
                    break
                if summary is not None:
                    cnt = summary.condition_passed.get(condition.name, 0)
                    summary.condition_passed[condition.name] = cnt + 1
                    summary.track(condition.name, cand["name"], ticker)
                cand["conditions"][condition.name] = fin_result.details

            if all_passed:
                results.append(self._build_result(target_date, cand))

        if summary is not None:
            summary.final_passed = len(results)

        return results

    def _build_result(
        self, target_date: date, cand: dict,
    ) -> ScreeningResult:
        """후보 dict → ScreeningResult 변환"""
        return ScreeningResult(
            run_date=target_date,
            ticker=cand["ticker"],
            name=cand["name"],
            close=cand["close"],
            volume=cand["volume"],
            market_cap=cand.get("market_cap", 0),
            change_pct=cand.get("change_pct", 0),
            sector=cand.get("sector", ""),
            passed_conditions=json.dumps(
                cand["conditions"],
                ensure_ascii=False,
                default=str,
            ),
        )

    def _save_results(self, results: list[ScreeningResult]) -> None:
        """스크리닝 결과 DB 저장 (동일 날짜 기존 결과 삭제 후 저장)"""
        if not results:
            return

        run_date = results[0].run_date
        with Session(engine) as session:
            # 동일 날짜 기존 결과 삭제
            from sqlmodel import select
            existing = session.exec(
                select(ScreeningResult).where(
                    ScreeningResult.run_date == run_date,
                ),
            ).all()
            for e in existing:
                session.delete(e)
            session.flush()

            for r in results:
                session.add(r)
            session.commit()
            # 세션 분리 후에도 속성 접근 가능하도록 미리 로드
            for r in results:
                session.refresh(r)
        logger.info("스크리닝 결과 %d건 저장 완료", len(results))

    def _save_summary(
        self,
        target_date: date,
        summary: FilterSummary,
    ) -> None:
        """스크리닝 퍼널 요약 DB 저장 (날짜별 upsert)"""
        from sqlmodel import select

        with Session(engine) as session:
            # 기존 동일 날짜 레코드 삭제
            existing = session.exec(
                select(ScreeningSummary).where(
                    ScreeningSummary.run_date == target_date,
                ),
            ).first()
            if existing:
                session.delete(existing)
                session.flush()

            row = ScreeningSummary(
                run_date=target_date,
                total_stocks=summary.total_stocks,
                first_filter_passed=summary.first_filter_passed,
                price_passed=summary.condition_passed.get("price", 0),
                volume_passed=summary.condition_passed.get("volume", 0),
                trend_passed=summary.condition_passed.get("trend", 0),
                golden_cross_passed=summary.condition_passed.get(
                    "golden_cross", 0,
                ),
                supply_demand_passed=summary.condition_passed.get(
                    "supply_demand", 0,
                ),
                financial_passed=summary.condition_passed.get(
                    "financial", 0,
                ),
                final_passed=summary.final_passed,
                strategy_name=summary.strategy_name,
            )
            session.add(row)
            session.commit()
        logger.info("스크리닝 퍼널 요약 저장 완료")

    # ── 로깅 헬퍼 ──

    _LOG_WIDTH = 70

    # ANSI 색상 코드
    _G = "\033[32m"   # 초록 (통과)
    _R = "\033[31m"   # 빨강 (미통과)
    _B = "\033[1m"    # 볼드
    _C = "\033[1;36m" # 볼드 시안 (섹션 헤더)
    _D = "\033[2m"    # 흐리게 (부가 정보)
    _0 = "\033[0m"    # 리셋

    @staticmethod
    def _format_cap(cap: float) -> str:
        """시가총액 포맷팅 (억/조 단위)"""
        if cap >= 1e12:
            return f"{cap / 1e12:,.1f}조"
        if cap >= 1e8:
            return f"{cap / 1e8:,.0f}억"
        if cap > 0:
            return f"{cap:,.0f}원"
        return "-"

    @classmethod
    def _mark(cls, val: object) -> str:
        """통과/미통과 마크 (색상 + 이모지)"""
        if val:
            return f"{cls._G}✅{cls._0}"
        return f"{cls._R}❌{cls._0}"

    @classmethod
    def _colored_val(cls, val: float) -> str:
        """양수/음수 색상 포맷"""
        if val > 0:
            return f"{cls._G}{val:+,.0f}{cls._0}"
        if val < 0:
            return f"{cls._R}{val:+,.0f}{cls._0}"
        return f"{val:+,.0f}"

    def _stock_names(self, summary: FilterSummary, stage: str, count: int) -> str:
        """10개 미만일 때 종목 목록 반환"""
        if count <= 0 or count >= _MAX_STAGE_TRACK:
            return ""
        stocks = summary.stage_stocks.get(stage, [])
        if not stocks:
            return ""
        return f"         {', '.join(stocks)}"

    # ── 조건 설명 매핑 ──

    _CONDITION_DESC: dict[str, str] = {
        "price": "P-1(종가>전일종가) AND P-2(종가>시가)",
        "volume": "V-1(≥3만주) AND (V-2(전일1.5배↑) OR V-3(5일MA돌파))",
        "trend": "20/60/120일 SMA 연속 상승 ≥2일 (모두 충족)",
        "golden_cross": "3/5/10일 SMA 상향돌파 (1개 이상)",
        "supply_demand": "S-1(프로그램순매수>0) OR S-2(외국인+기관순매수>0)",
        "financial": "F-1(YoY↑) AND F-2(QoQ↑) AND F-3(연간적자전환❌) AND F-4(분기적자전환❌)",
    }

    # ── 퍼널 요약 ──

    def _log_summary(self, summary: FilterSummary) -> None:
        """필터링 퍼널 요약 로깅 (단계별 통과율 포함)"""
        s = summary
        W = self._LOG_WIDTH
        prev_count = s.total_stocks

        lines = [
            "",
            "═" * W,
            f"  스크리닝 퍼널 요약 (전략: {s.strategy_name})",
            "═" * W,
            "",
            f"  전체 종목: {s.total_stocks:,}개",
        ]

        # 소요시간 포맷 헬퍼
        def _elapsed(key: str) -> str:
            t = s.stage_elapsed.get(key)
            if t is None:
                return ""
            if t >= 60:
                return f" ({t / 60:.1f}분)"
            return f" ({t:.2f}초)"

        # 종목 마스터
        lines.append("")
        lines.append(f"  [준비] 종목 마스터 갱신{_elapsed('종목마스터')}")

        # 1차 필터
        pct = (s.first_filter_passed / prev_count * 100) if prev_count > 0 else 0
        lines.append("")
        lines.append(f"  [1차 필터] 전종목 벡터 연산{_elapsed('1차 필터')}")
        lines.append(
            "    조건: P-1(종가>전일종가) AND P-2(종가>시가) AND 거래량≥3만주",
        )
        lines.append(
            f"    결과: {s.first_filter_passed:,}개 통과 ({pct:.1f}%)",
        )
        sl = self._stock_names(s, "first_filter", s.first_filter_passed)
        if sl:
            lines.append(sl)
        prev_count = s.first_filter_passed

        # 2차 필터 (동적)
        if self._strategy.technical_conditions:
            lines.append("")
            lines.append(
                f"  [2차 필터] 기술적 분석{_elapsed('2차 필터')}",
            )
            for condition in self._strategy.technical_conditions:
                cname = condition.name
                desc = self._CONDITION_DESC.get(cname, "")
                count = s.condition_passed.get(cname, 0)
                pct = (count / prev_count * 100) if prev_count > 0 else 0
                lines.append(
                    f"    {cname}: {count:,}개 통과 ({pct:.1f}%)",
                )
                if desc:
                    lines.append(f"      조건: {desc}")
                sl = self._stock_names(s, cname, count)
                if sl:
                    lines.append(sl)
                prev_count = count

        # 3차 필터 (동적)
        if self._strategy.supply_conditions:
            lines.append("")
            lines.append(
                f"  [3차 필터] 수급 분석{_elapsed('3차 필터')}",
            )
            for condition in self._strategy.supply_conditions:
                cname = condition.name
                desc = self._CONDITION_DESC.get(cname, "")
                count = s.condition_passed.get(cname, 0)
                pct = (count / prev_count * 100) if prev_count > 0 else 0
                lines.append(
                    f"    {cname}: {count:,}개 통과 ({pct:.1f}%)",
                )
                if desc:
                    lines.append(f"      조건: {desc}")
                sl = self._stock_names(s, cname, count)
                if sl:
                    lines.append(sl)
                prev_count = count

        # 4차 필터 (동적)
        if self._strategy.financial_conditions:
            lines.append("")
            lines.append(
                f"  [4차 필터] 실적 분석{_elapsed('4차 필터')}",
            )
            for condition in self._strategy.financial_conditions:
                cname = condition.name
                desc = self._CONDITION_DESC.get(cname, "")
                count = s.condition_passed.get(cname, 0)
                pct = (count / prev_count * 100) if prev_count > 0 else 0
                lines.append(
                    f"    {cname}: {count:,}개 통과 ({pct:.1f}%)",
                )
                if desc:
                    lines.append(f"      조건: {desc}")
                sl = self._stock_names(s, cname, count)
                if sl:
                    lines.append(sl)
                prev_count = count

        lines.append("")
        lines.append(
            f"  >> 최종 선정: {s.final_passed:,}개{_elapsed('전체')}",
        )
        lines.append("═" * W)
        logger.info("\n".join(lines))

    # ── 최종 종목 상세 ──

    def _log_final_results(self, results: list[ScreeningResult]) -> None:
        """최종 선정 종목별 조건 부합 상세 로깅"""
        if not results:
            return

        W = self._LOG_WIDTH
        lines = [
            "",
            "═" * W,
            f"  최종 선정 종목 상세 ({len(results)}건)",
            "═" * W,
        ]

        for idx, r in enumerate(results, 1):
            conds = r.conditions_dict
            cap_str = self._format_cap(r.market_cap)

            # 종목 헤더 (볼드 강조)
            lines.append("")
            lines.append(
                f"{self._B}─ {idx}. {r.name} ({r.ticker}){self._0}",
            )
            lines.append(
                f"  종가 {self._B}{r.close:,.0f}원{self._0}"
                f"  /  거래량 {r.volume:,}주"
                f"  /  시총 {cap_str}",
            )

            # 조건별 상세 포맷팅
            for key, details in conds.items():
                if key == "price_preliminary" or not isinstance(details, dict):
                    continue
                lines.append("")
                for cl in self._fmt_condition(key, details):
                    lines.append(f"  {cl}")

        lines.append("")
        lines.append("─" * W)
        logger.info("\n".join(lines))

    def _fmt_condition(self, key: str, d: dict) -> list[str]:
        """조건 유형별 상세 포맷팅 분기"""
        formatters = {
            "price": self._fmt_price,
            "volume": self._fmt_volume,
            "trend": self._fmt_trend,
            "golden_cross": self._fmt_golden_cross,
            "supply_demand": self._fmt_supply_demand,
            "financial": self._fmt_financial,
        }
        fmt = formatters.get(key)
        if fmt:
            return fmt(d)
        # 알 수 없는 조건은 기본 출력
        detail_str = ", ".join(f"{k}: {v}" for k, v in d.items())
        return [f"[{key}] {detail_str}"]

    def _fmt_price(self, d: dict) -> list[str]:
        """가격 조건 포맷팅"""
        close = d.get("close", 0)
        prev = d.get("prev_close", 0)
        opn = d.get("open", 0)
        chg = ((close - prev) / prev * 100) if prev > 0 else 0
        return [
            f"{self._C}[가격]{self._0} 전일 대비 상승 + 양봉",
            f"  {self._mark(d.get('P-1_종가>전일종가'))} P-1 종가 > 전일종가"
            f"   {close:,.0f} > {prev:,.0f} ({chg:+.2f}%)",
            f"  {self._mark(d.get('P-2_종가>시가'))} P-2 종가 > 시가"
            f"       {close:,.0f} > {opn:,.0f}",
        ]

    def _fmt_volume(self, d: dict) -> list[str]:
        """거래량 조건 포맷팅"""
        vol = d.get("volume", 0)
        prev = d.get("prev_volume", 0)
        ma5 = d.get("volume_ma5", 0)
        ratio = (vol / prev) if prev > 0 else 0
        return [
            f"{self._C}[거래량]{self._0} V-1 AND (V-2 OR V-3)",
            f"  {self._mark(d.get('V-1_3만주이상'))} V-1 최소거래량"
            f"   {vol:,}주 (≥ {settings.VOLUME_MIN:,})",
            f"  {self._mark(d.get('V-2_전일1.5배'))} V-2 전일대비"
            f"     {vol:,} / {prev:,} = {ratio:.1f}배"
            f" (≥ {settings.VOLUME_RATIO}배)",
            f"  {self._mark(d.get('V-3_5일MA돌파'))} V-3 5일MA 돌파"
            f"   {vol:,} vs MA {ma5:,.0f}",
        ]

    def _fmt_trend(self, d: dict) -> list[str]:
        """추세 조건 포맷팅"""
        min_cnt = settings.TREND_MIN_COUNT
        lines = [
            f"{self._C}[추세]{self._0}"
            f" 이평선 연속 상승 (최소 {min_cnt}일, 모두 충족)",
        ]
        for period in settings.TREND_PERIODS:
            cnt = d.get(f"T_{period}일_연속상승", 0)
            ok = d.get(f"T_{period}일_통과", False)
            lines.append(
                f"  {self._mark(ok)} {period:>3d}일선"
                f"  {cnt}일 연속 ↑",
            )
        return lines

    def _fmt_golden_cross(self, d: dict) -> list[str]:
        """골든크로스 조건 포맷팅"""
        lines = [
            f"{self._C}[골든크로스]{self._0}"
            f" 종가 SMA 상향돌파 (1개 이상 충족)",
        ]
        for period in settings.GOLDEN_CROSS_PERIODS:
            ok = d.get(f"G_{period}일_통과", False)
            sma = d.get(f"G_{period}일_SMA당일", 0)
            below = d.get(f"G_{period}일_전일아래", False)
            above = d.get(f"G_{period}일_당일위", False)
            if ok:
                lines.append(
                    f"  {self._mark(ok)} {period:>2d}일선"
                    f"  SMA {sma:,.0f} 돌파",
                )
            else:
                # 미통과 사유 (흐리게)
                reasons = []
                if not below:
                    reasons.append("전일 이미 위")
                if not above:
                    reasons.append("당일 미돌파")
                lines.append(
                    f"  {self._mark(ok)} {period:>2d}일선"
                    f"  SMA {sma:,.0f}"
                    f" {self._D}({', '.join(reasons)}){self._0}",
                )
        return lines

    def _fmt_supply_demand(self, d: dict) -> list[str]:
        """수급 조건 포맷팅"""
        lines = [
            f"{self._C}[수급]{self._0}"
            f" S-1(프로그램) OR S-2(외국인+기관)",
        ]

        # S-1: 프로그램 순매수
        s1 = d.get("S-1_프로그램순매수", False)
        lines.append(
            f"  {self._mark(s1)} S-1 프로그램 순매수 (모든 기간 > 0)",
        )
        parts = []
        for p in settings.SUPPLY_DEMAND_PERIODS:
            val = d.get(f"프로그램_{p}일합계", 0)
            mk = self._mark(d.get(f"프로그램_{p}일_순매수", False))
            parts.append(f"{p}일 {self._colored_val(val)} {mk}")
        lines.append(f"       {' │ '.join(parts)}")

        # S-2: 외국인 AND 기관
        s2 = d.get("S-2_외국인AND기관", False)
        lines.append(
            f"  {self._mark(s2)} S-2 외국인 AND 기관 (모든 기간 > 0)",
        )
        for label in ["외국인", "기관"]:
            parts = []
            for p in settings.SUPPLY_DEMAND_PERIODS:
                val = d.get(f"{label}_{p}일합계", 0)
                mk = self._mark(d.get(f"{label}_{p}일_순매수", False))
                parts.append(f"{p}일 {self._colored_val(val)} {mk}")
            lines.append(f"       {label}: {' │ '.join(parts)}")

        return lines

    def _fmt_financial(self, d: dict) -> list[str]:
        """실적 조건 포맷팅"""
        lines = [
            f"{self._C}[실적]{self._0}"
            f" F-1(YoY↑) AND F-2(QoQ↑) AND F-3(연간적자전환❌) AND F-4(분기적자전환❌)",
        ]

        # F-1: YoY 영업이익 증가
        f1 = d.get("F-1_YoY증가", False)
        latest = d.get("F-1_최근분기", 0)
        yoy = d.get("F-1_전년동기", 0)
        lines.append(
            f"  {self._mark(f1)} F-1 YoY 증가"
            f"   {self._colored_val(latest)} > {self._colored_val(yoy)}",
        )

        # F-2: QoQ 영업이익 증가
        f2 = d.get("F-2_QoQ증가", False)
        q_latest = d.get("F-2_최근분기", 0)
        q_prev = d.get("F-2_직전분기", 0)
        lines.append(
            f"  {self._mark(f2)} F-2 QoQ 증가"
            f"   {self._colored_val(q_latest)} > {self._colored_val(q_prev)}",
        )

        # F-3: 연간 적자전환
        f3 = d.get("F-3_연간적자전환없음", False)
        deficit3 = d.get("F-3_적자전환", False)
        a_cur = d.get("F-3_당년영업이익", 0)
        a_prev = d.get("F-3_전년영업이익", 0)
        lines.append(
            f"  {self._mark(f3)} F-3 연간 적자전환 없음"
            f"   당년 {self._colored_val(a_cur)} / 전년 {self._colored_val(a_prev)}",
        )

        # F-4: 분기 적자전환
        f4 = d.get("F-4_분기적자전환없음", False)
        deficit4 = d.get("F-4_적자전환", False)
        q4_cur = d.get("F-4_최근분기", 0)
        q4_prev = d.get("F-4_직전분기", 0)
        lines.append(
            f"  {self._mark(f4)} F-4 분기 적자전환 없음"
            f"   최근 {self._colored_val(q4_cur)} / 직전 {self._colored_val(q4_prev)}",
        )

        return lines
