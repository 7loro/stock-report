"""이평선 추세 조건 (FRD 3.1.3)"""

import numpy as np
import pandas as pd

from screening.config import settings
from screening.engine.base import ConditionResult, ScreeningCondition


class TrendCondition(ScreeningCondition):
    """
    T-1~3: 20/60/120일 SMA가 상승 추세
    각 SMA의 diff()가 당일부터 역방향으로 연속 양수인 횟수 >= 2
    모든 기간 충족해야 통과
    """

    @property
    def name(self) -> str:
        return "trend"

    def evaluate(
        self,
        ticker: str,
        ohlcv_df: pd.DataFrame,
        investor_df: pd.DataFrame | None = None,
        **kwargs: object,
    ) -> ConditionResult:
        periods = settings.TREND_PERIODS
        min_count = settings.TREND_MIN_COUNT
        max_period = max(periods)

        if len(ohlcv_df) < max_period + min_count:
            return ConditionResult(passed=False, details={"error": "데이터 부족"})

        details: dict = {}
        all_passed = True

        for period in periods:
            # SMA 계산
            sma = ohlcv_df["close"].rolling(window=period).mean()
            sma_diff = sma.diff()

            # 당일부터 역방향으로 연속 양수 카운트 (numpy 벡터화)
            arr = sma_diff.values[::-1]
            valid_positive = np.isfinite(arr) & (arr > 0)
            if valid_positive.all():
                consecutive = len(valid_positive)
            else:
                consecutive = int(np.argmin(valid_positive))

            passed = consecutive >= min_count
            if not passed:
                all_passed = False

            details[f"T_{period}일_연속상승"] = consecutive
            details[f"T_{period}일_통과"] = passed

        return ConditionResult(passed=all_passed, details=details)
