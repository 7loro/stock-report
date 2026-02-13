"""가격 조건 (FRD 3.1.2)"""

import pandas as pd

from screening.engine.base import ConditionResult, ScreeningCondition


class PriceCondition(ScreeningCondition):
    """
    P-1: 종가 > 전일 종가 (양봉)
    P-2: 종가 > 시가 (장중 상승)
    두 조건 모두 충족해야 통과
    """

    @property
    def name(self) -> str:
        return "price"

    def evaluate(
        self,
        ticker: str,
        ohlcv_df: pd.DataFrame,
        investor_df: pd.DataFrame | None = None,
        **kwargs: object,
    ) -> ConditionResult:
        if len(ohlcv_df) < 2:
            return ConditionResult(passed=False, details={"error": "데이터 부족"})

        today = ohlcv_df.iloc[-1]
        yesterday = ohlcv_df.iloc[-2]

        p1 = bool(today["close"] > yesterday["close"])  # 전일 대비 상승
        p2 = bool(today["close"] > today["open"])  # 시가 대비 상승

        return ConditionResult(
            passed=p1 and p2,
            details={
                "P-1_종가>전일종가": p1,
                "P-2_종가>시가": p2,
                "close": float(today["close"]),
                "prev_close": float(yesterday["close"]),
                "open": float(today["open"]),
            },
        )
