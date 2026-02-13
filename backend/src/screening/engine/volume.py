"""거래량 조건 (FRD 3.1.1)"""

import pandas as pd

from screening.config import settings
from screening.engine.base import ConditionResult, ScreeningCondition


class VolumeCondition(ScreeningCondition):
    """
    거래량 조건: 3만주 이상 AND (전일대비 1.5배↑ OR 5일 MA 돌파)
    """

    @property
    def name(self) -> str:
        return "volume"

    def evaluate(
        self,
        ticker: str,
        ohlcv_df: pd.DataFrame,
        investor_df: pd.DataFrame | None = None,
        **kwargs: object,
    ) -> ConditionResult:
        if len(ohlcv_df) < 2:
            return ConditionResult(passed=False, details={"error": "데이터 부족"})

        ma_period = settings.VOLUME_MA_PERIOD
        today = ohlcv_df.iloc[-1]
        yesterday = ohlcv_df.iloc[-2]

        volume = int(today["volume"])
        prev_volume = int(yesterday["volume"])

        # 최소 거래량 (3만주 이상)
        min_vol = volume >= settings.VOLUME_MIN

        # 전일 대비 1.5배
        ratio = prev_volume > 0 and volume >= prev_volume * settings.VOLUME_RATIO

        # 5일 MA 돌파
        ma_break = False
        volume_ma = 0.0
        if len(ohlcv_df) >= ma_period:
            volume_ma = float(ohlcv_df["volume"].tail(ma_period).mean())
            ma_break = volume > volume_ma

        passed = min_vol and (ratio or ma_break)

        return ConditionResult(
            passed=passed,
            details={
                "V-1_3만주이상": min_vol,
                "V-2_전일1.5배": ratio,
                "V-3_5일MA돌파": ma_break,
                "volume": volume,
                "prev_volume": prev_volume,
                "volume_ma5": round(volume_ma, 1),
            },
        )
