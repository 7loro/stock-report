"""투자자별 매매 동향 모델"""

from datetime import date as date_type

from sqlalchemy import Column as SAColumn, Date, Float, String
from sqlmodel import Field, SQLModel


class InvestorTrading(SQLModel, table=True):
    """투자자별 순매수 + 프로그램 순매수 테이블"""

    __tablename__ = "investor_trading"

    ticker: str = Field(
        sa_column=SAColumn(String(10), primary_key=True),
        description="종목 코드",
    )
    date: date_type = Field(
        sa_column=SAColumn("date", Date, primary_key=True),
        description="거래일",
    )
    individual: float = Field(default=0, description="개인 순매수")
    foreign_val: float = Field(
        sa_column=SAColumn("foreign", Float, default=0),
        description="외국인 순매수",
    )
    institution: float = Field(default=0, description="기관 순매수")
    program_net_buy: float = Field(default=0, description="프로그램 순매수")
