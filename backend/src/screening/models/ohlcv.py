"""일별 OHLCV 모델"""

from datetime import date as date_type

from sqlalchemy import Column as SAColumn, Float, Integer, String, Date
from sqlmodel import Field, SQLModel


class DailyOHLCV(SQLModel, table=True):
    """일별 시가/고가/저가/종가/거래량 테이블"""

    __tablename__ = "daily_ohlcv"

    ticker: str = Field(
        sa_column=SAColumn(String(10), primary_key=True),
        description="종목 코드",
    )
    date: date_type = Field(
        sa_column=SAColumn("date", Date, primary_key=True),
        description="거래일",
    )
    open_price: float = Field(
        sa_column=SAColumn("open", Float),
        description="시가",
    )
    high: float = Field(default=0, description="고가")
    low: float = Field(default=0, description="저가")
    close: float = Field(default=0, description="종가")
    volume: int = Field(default=0, description="거래량")
