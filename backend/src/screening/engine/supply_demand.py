"""수급 조건 (FRD 3.1.5)"""

import pandas as pd

from screening.config import settings
from screening.engine.base import ConditionResult, ScreeningCondition


class SupplyDemandCondition(ScreeningCondition):
    """
    S-1: 프로그램 합산 순매수 (2/5/20일) — 모든 기간 합계 > 0
    S-2: 외국인 합산 순매수 (2/5/20일) AND 기관 합산 순매수 (2/5/20일)
    S-1 OR S-2 충족 시 통과
    """

    @property
    def name(self) -> str:
        return "supply_demand"

    def evaluate(
        self,
        ticker: str,
        ohlcv_df: pd.DataFrame,
        investor_df: pd.DataFrame | None = None,
        **kwargs: object,
    ) -> ConditionResult:
        if investor_df is None or investor_df.empty:
            return ConditionResult(passed=False, details={"error": "투자자 데이터 없음"})

        periods = settings.SUPPLY_DEMAND_PERIODS

        # S-1: 프로그램 합산 순매수 — 모든 기간 합계 > 0
        s1, s1_details = self._check_net_buy(investor_df, "program_net_buy", "프로그램", periods)

        # S-2: 외국인 AND 기관 합산 순매수
        s2_foreign, s2f_details = self._check_net_buy(investor_df, "foreign", "외국인", periods)
        s2_inst, s2i_details = self._check_net_buy(investor_df, "institution", "기관", periods)
        s2 = s2_foreign and s2_inst

        return ConditionResult(
            passed=s1 or s2,
            details={
                "S-1_프로그램순매수": s1,
                **s1_details,
                "S-2_외국인AND기관": s2,
                "S-2_외국인순매수": s2_foreign,
                **s2f_details,
                "S-2_기관순매수": s2_inst,
                **s2i_details,
            },
        )

    @staticmethod
    def _check_net_buy(
        df: pd.DataFrame,
        column: str,
        label: str,
        periods: list[int],
    ) -> tuple[bool, dict]:
        """지정 컬럼의 기간별 합산 순매수 검사. 모든 기간 합계 > 0이면 통과."""
        details: dict = {}
        all_positive = True

        if column not in df.columns:
            return False, {f"{label}_데이터없음": True}

        for period in periods:
            if len(df) >= period:
                total = float(df[column].tail(period).sum())
                is_positive = total > 0
                details[f"{label}_{period}일합계"] = round(total, 0)
                details[f"{label}_{period}일_순매수"] = is_positive
                if not is_positive:
                    all_positive = False
            else:
                details[f"{label}_{period}일_순매수"] = False
                all_positive = False

        return all_positive, details
