"""실적/컨센서스 조건 (F-1 ~ F-4)

F-1: 전년 동기 대비 영업이익 증가 (YoY)
F-2: 직전 분기 대비 영업이익 증가 (QoQ)
F-3: 연간 영업이익 적자전환 여부
F-4: 분기 영업이익 적자전환 여부

모두 AND 조건. 데이터 부족 시 탈락.
"""

import pandas as pd

from screening.engine.base import ConditionResult, ScreeningCondition


class FinancialCondition(ScreeningCondition):
    """4차 필터: 실적/컨센서스 조건 (F-1 ~ F-4)"""

    @property
    def name(self) -> str:
        return "financial"

    def evaluate(
        self,
        ticker: str,
        ohlcv_df: pd.DataFrame,
        investor_df: pd.DataFrame | None = None,
        **kwargs: object,
    ) -> ConditionResult:
        quarterly_df = kwargs.get("quarterly_df")
        annual_df = kwargs.get("annual_df")

        details: dict = {}

        # F-1: YoY 영업이익 증가 (최근 분기 vs 전년 동기)
        f1, f1_details = self._check_yoy(quarterly_df)
        details["F-1_YoY증가"] = f1
        details.update(f1_details)

        # F-2: QoQ 영업이익 증가 (최근 분기 vs 직전 분기)
        f2, f2_details = self._check_qoq(quarterly_df)
        details["F-2_QoQ증가"] = f2
        details.update(f2_details)

        # F-3: 연간 영업이익 적자전환 여부
        f3, f3_details = self._check_annual_deficit(annual_df)
        details["F-3_연간적자전환없음"] = f3
        details.update(f3_details)

        # F-4: 분기 영업이익 적자전환 여부
        f4, f4_details = self._check_quarterly_deficit(quarterly_df)
        details["F-4_분기적자전환없음"] = f4
        details.update(f4_details)

        return ConditionResult(
            passed=f1 and f2 and f3 and f4,
            details=details,
        )

    @staticmethod
    def _check_yoy(
        quarterly_df: pd.DataFrame | None,
    ) -> tuple[bool, dict]:
        """F-1: 전년 동기 대비 영업이익 증가 (strictly greater)

        최근 5분기 이상 필요 (현재 분기 + 전년 동기).
        """
        if quarterly_df is None or quarterly_df.empty:
            return False, {"F-1_사유": "분기 데이터 없음"}

        if "operating_income" not in quarterly_df.columns:
            return False, {"F-1_사유": "영업이익 컬럼 없음"}

        # 최소 5분기 필요 (YoY 비교 = 4분기 전)
        if len(quarterly_df) < 5:
            return False, {"F-1_사유": f"분기 데이터 부족 ({len(quarterly_df)}건)"}

        latest = float(quarterly_df["operating_income"].iloc[-1])
        yoy_target = float(quarterly_df["operating_income"].iloc[-5])

        passed = latest > yoy_target
        return passed, {
            "F-1_최근분기": latest,
            "F-1_전년동기": yoy_target,
        }

    @staticmethod
    def _check_qoq(
        quarterly_df: pd.DataFrame | None,
    ) -> tuple[bool, dict]:
        """F-2: 직전 분기 대비 영업이익 증가 (strictly greater)"""
        if quarterly_df is None or quarterly_df.empty:
            return False, {"F-2_사유": "분기 데이터 없음"}

        if "operating_income" not in quarterly_df.columns:
            return False, {"F-2_사유": "영업이익 컬럼 없음"}

        if len(quarterly_df) < 2:
            return False, {"F-2_사유": f"분기 데이터 부족 ({len(quarterly_df)}건)"}

        latest = float(quarterly_df["operating_income"].iloc[-1])
        previous = float(quarterly_df["operating_income"].iloc[-2])

        passed = latest > previous
        return passed, {
            "F-2_최근분기": latest,
            "F-2_직전분기": previous,
        }

    @staticmethod
    def _check_annual_deficit(
        annual_df: pd.DataFrame | None,
    ) -> tuple[bool, dict]:
        """F-3: 연간 영업이익 적자전환 여부

        직전 연도 흑자(>0) → 현재 연도 적자(<0) 이면 탈락.
        이미 적자였으면 해당 없음 (통과).
        """
        if annual_df is None or annual_df.empty:
            return False, {"F-3_사유": "연간 데이터 없음"}

        if "operating_income" not in annual_df.columns:
            return False, {"F-3_사유": "영업이익 컬럼 없음"}

        if len(annual_df) < 2:
            return False, {"F-3_사유": f"연간 데이터 부족 ({len(annual_df)}건)"}

        current = float(annual_df["operating_income"].iloc[-1])
        previous = float(annual_df["operating_income"].iloc[-2])

        # 적자전환 = 직전 흑자(>0) AND 현재 적자(<0)
        deficit_turn = previous > 0 and current < 0
        passed = not deficit_turn

        return passed, {
            "F-3_당년영업이익": current,
            "F-3_전년영업이익": previous,
            "F-3_적자전환": deficit_turn,
        }

    @staticmethod
    def _check_quarterly_deficit(
        quarterly_df: pd.DataFrame | None,
    ) -> tuple[bool, dict]:
        """F-4: 분기 영업이익 적자전환 여부

        직전 분기 흑자(>0) → 최근 분기 적자(<0) 이면 탈락.
        이미 적자였으면 해당 없음 (통과).
        """
        if quarterly_df is None or quarterly_df.empty:
            return False, {"F-4_사유": "분기 데이터 없음"}

        if "operating_income" not in quarterly_df.columns:
            return False, {"F-4_사유": "영업이익 컬럼 없음"}

        if len(quarterly_df) < 2:
            return False, {"F-4_사유": f"분기 데이터 부족 ({len(quarterly_df)}건)"}

        current = float(quarterly_df["operating_income"].iloc[-1])
        previous = float(quarterly_df["operating_income"].iloc[-2])

        # 적자전환 = 직전 흑자(>0) AND 현재 적자(<0)
        deficit_turn = previous > 0 and current < 0
        passed = not deficit_turn

        return passed, {
            "F-4_최근분기": current,
            "F-4_직전분기": previous,
            "F-4_적자전환": deficit_turn,
        }
