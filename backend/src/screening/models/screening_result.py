"""스크리닝 결과 모델"""

import json
from datetime import date as date_type, datetime

from sqlalchemy import Column as SAColumn, Date, DateTime, Float, Integer, String, Text
from sqlmodel import Field, SQLModel


class ScreeningResult(SQLModel, table=True):
    """스크리닝 결과 테이블"""

    __tablename__ = "screening_results"

    id: int | None = Field(default=None, primary_key=True)
    run_date: date_type = Field(
        sa_column=SAColumn("run_date", Date, index=True),
        description="스크리닝 실행일",
    )
    ticker: str = Field(max_length=10, description="종목 코드")
    name: str = Field(max_length=100, description="종목명")
    close: float = Field(default=0, description="종가")
    volume: int = Field(default=0, description="거래량")
    market_cap: float = Field(default=0, description="시가총액(원)")
    change_pct: float = Field(default=0, description="등락률(%)")
    sector: str = Field(default="", max_length=50, description="업종명")
    passed_conditions: str = Field(
        default="{}",
        sa_column=SAColumn("passed_conditions", Text),
        description="통과 조건 상세 (JSON)",
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=SAColumn("created_at", DateTime, default=datetime.now),
        description="생성 시각",
    )

    @property
    def conditions_dict(self) -> dict:
        """통과 조건을 dict로 반환"""
        return json.loads(self.passed_conditions)

    @conditions_dict.setter
    def conditions_dict(self, value: dict) -> None:
        """dict를 JSON 문자열로 저장"""
        self.passed_conditions = json.dumps(value, ensure_ascii=False)


class ScreeningSummary(SQLModel, table=True):
    """스크리닝 퍼널 요약 테이블 (날짜별 1건)"""

    __tablename__ = "screening_summary"

    id: int | None = Field(default=None, primary_key=True)
    run_date: date_type = Field(
        sa_column=SAColumn("run_date", Date, unique=True, index=True),
        description="스크리닝 실행일",
    )
    total_stocks: int = Field(default=0, description="전체 종목 수")
    first_filter_passed: int = Field(default=0, description="1차 필터 통과")
    price_passed: int = Field(default=0, description="가격 조건 통과")
    volume_passed: int = Field(default=0, description="거래량 조건 통과")
    trend_passed: int = Field(default=0, description="추세 조건 통과")
    golden_cross_passed: int = Field(default=0, description="골든크로스 통과")
    supply_demand_passed: int = Field(default=0, description="수급 조건 통과")
    financial_passed: int = Field(default=0, description="실적 조건 통과")
    final_passed: int = Field(default=0, description="최종 통과")
    strategy_name: str = Field(default="", max_length=50, description="전략명")
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=SAColumn("created_at", DateTime, default=datetime.now),
        description="생성 시각",
    )
