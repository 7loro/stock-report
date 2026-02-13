"""스크리닝 조건 추상 인터페이스"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class ConditionResult:
    """조건 평가 결과"""

    passed: bool
    details: dict = field(default_factory=dict)


class ScreeningCondition(ABC):
    """개별 스크리닝 조건 ABC"""

    @property
    @abstractmethod
    def name(self) -> str:
        """조건명"""
        ...

    @abstractmethod
    def evaluate(
        self,
        ticker: str,
        ohlcv_df: pd.DataFrame,
        investor_df: pd.DataFrame | None = None,
        **kwargs: object,
    ) -> ConditionResult:
        """조건 평가 실행"""
        ...
