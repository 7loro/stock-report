"""골든크로스 조건 (FRD 3.1.4)"""

import pandas as pd

from screening.config import settings
from screening.engine.base import ConditionResult, ScreeningCondition


class GoldenCrossCondition(ScreeningCondition):
    """
    G-1~3: 종가가 3/5/10일 SMA 중 하나라도 상향 돌파
    전일: close <= SMA(N) (아래)
    당일: close > SMA(N) (위)
    3개 기간 중 하나 이상 충족하면 통과
    """

    @property
    def name(self) -> str:
        return "golden_cross"

    def evaluate(
        self,
        ticker: str,
        ohlcv_df: pd.DataFrame,
        investor_df: pd.DataFrame | None = None,
        **kwargs: object,
    ) -> ConditionResult:
        periods = settings.GOLDEN_CROSS_PERIODS
        max_period = max(periods)

        if len(ohlcv_df) < max_period + 2:
            return ConditionResult(passed=False, details={"error": "데이터 부족"})

        today = ohlcv_df.iloc[-1]
        yesterday = ohlcv_df.iloc[-2]
        details: dict = {}
        any_passed = False

        for period in periods:
            sma = ohlcv_df["close"].rolling(window=period).mean()
            sma_today = sma.iloc[-1]
            sma_yesterday = sma.iloc[-2]

            # 전일: 종가 <= SMA (아래)
            below_yesterday = float(yesterday["close"]) <= float(sma_yesterday)
            # 당일: 종가 > SMA (위)
            above_today = float(today["close"]) > float(sma_today)

            passed = below_yesterday and above_today
            if passed:
                any_passed = True

            details[f"G_{period}일_전일아래"] = below_yesterday
            details[f"G_{period}일_당일위"] = above_today
            details[f"G_{period}일_통과"] = passed
            details[f"G_{period}일_SMA당일"] = round(float(sma_today), 2)

        return ConditionResult(passed=any_passed, details=details)
