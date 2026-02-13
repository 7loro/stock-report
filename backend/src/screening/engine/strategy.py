"""검색식(Strategy) 정의 — 2차/3차/4차 필터 조건 조합을 유연하게 구성"""

from dataclasses import dataclass, field

from screening.engine.base import ScreeningCondition
from screening.engine.price import PriceCondition
from screening.engine.volume import VolumeCondition
from screening.engine.trend import TrendCondition
from screening.engine.golden_cross import GoldenCrossCondition
from screening.engine.supply_demand import SupplyDemandCondition
from screening.engine.financial import FinancialCondition


@dataclass
class ScreeningStrategy:
    """스크리닝 전략 — 2차/3차/4차 필터 조건 조합 정의

    - technical_conditions: 2차 필터 조건 (순서대로 AND 평가)
    - supply_conditions: 3차 필터 조건 (비어있으면 3차 스킵)
    - financial_conditions: 4차 필터 조건 (비어있으면 4차 스킵)
    """

    name: str
    description: str
    # 2차 필터 조건 (순서대로 AND 평가)
    technical_conditions: list[ScreeningCondition] = field(default_factory=list)
    # 3차 필터 조건 (비어있으면 3차 스킵)
    supply_conditions: list[ScreeningCondition] = field(default_factory=list)
    # 4차 필터 조건 (비어있으면 4차 스킵)
    financial_conditions: list[ScreeningCondition] = field(default_factory=list)


# ── 사전 정의 전략 ──

# 기본 전략: 현재 파이프라인과 동일
DEFAULT = ScreeningStrategy(
    name="DEFAULT",
    description="기본 검색식 (가격 + 거래량 + 추세 + 골든크로스 + 수급 + 실적)",
    technical_conditions=[
        PriceCondition(),
        VolumeCondition(),
        TrendCondition(),
        GoldenCrossCondition(),
    ],
    supply_conditions=[
        SupplyDemandCondition(),
    ],
    financial_conditions=[
        FinancialCondition(),
    ],
)

# 거래량 급등 전략
VOLUME_BREAKOUT = ScreeningStrategy(
    name="VOLUME_BREAKOUT",
    description="거래량 급등 검색식 (가격 + 거래량만)",
    technical_conditions=[
        PriceCondition(),
        VolumeCondition(),
    ],
)

# 골든크로스 단순 전략
GOLDEN_CROSS_SIMPLE = ScreeningStrategy(
    name="GOLDEN_CROSS_SIMPLE",
    description="골든크로스 단순 검색식 (가격 + 거래량 + 골든크로스)",
    technical_conditions=[
        PriceCondition(),
        VolumeCondition(),
        GoldenCrossCondition(),
    ],
)

# 추세 추종 + 수급 전략
TREND_FOLLOWING = ScreeningStrategy(
    name="TREND_FOLLOWING",
    description="추세 추종 검색식 (가격 + 거래량 + 추세 + 수급)",
    technical_conditions=[
        PriceCondition(),
        VolumeCondition(),
        TrendCondition(),
    ],
    supply_conditions=[
        SupplyDemandCondition(),
    ],
)

# ── 전략 레지스트리 ──

STRATEGIES: dict[str, ScreeningStrategy] = {
    s.name: s
    for s in [DEFAULT, VOLUME_BREAKOUT, GOLDEN_CROSS_SIMPLE, TREND_FOLLOWING]
}


def get_strategy(name: str) -> ScreeningStrategy:
    """이름으로 전략 조회. 없으면 KeyError 발생."""
    if name not in STRATEGIES:
        available = ", ".join(STRATEGIES.keys())
        raise KeyError(f"전략 '{name}' 없음. 사용 가능: {available}")
    return STRATEGIES[name]


def list_strategies() -> list[str]:
    """등록된 전략 이름 목록 반환"""
    return list(STRATEGIES.keys())
