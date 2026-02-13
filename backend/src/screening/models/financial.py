"""재무제표 모델"""

from sqlalchemy import Column as SAColumn, Boolean, Float, String
from sqlmodel import Field, SQLModel


class FinancialStatement(SQLModel, table=True):
    """재무제표 테이블 (분기/연간 손익계산서)"""

    __tablename__ = "financial_statements"

    ticker: str = Field(
        sa_column=SAColumn(String(10), primary_key=True),
        description="종목 코드",
    )
    period: str = Field(
        sa_column=SAColumn(String(20), primary_key=True),
        description="기간 (분기: '2025/09', 연간: '2025')",
    )
    freq: str = Field(
        max_length=1,
        description="Q(분기) 또는 Y(연간)",
    )
    revenue: float = Field(
        sa_column=SAColumn(Float, default=0),
        description="매출액 (억원)",
    )
    operating_income: float = Field(
        sa_column=SAColumn(Float, default=0),
        description="영업이익 (억원)",
    )
    net_income: float = Field(
        sa_column=SAColumn(Float, default=0),
        description="당기순이익 (억원)",
    )
    is_estimate: bool = Field(
        sa_column=SAColumn(Boolean, default=False),
        description="컨센서스 추정치 여부",
    )
