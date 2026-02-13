"""종목 마스터 모델"""

from sqlmodel import Field, SQLModel


class Stock(SQLModel, table=True):
    """종목 정보 테이블"""

    __tablename__ = "stocks"

    ticker: str = Field(primary_key=True, max_length=10, description="종목 코드")
    name: str = Field(max_length=100, description="종목명")
    market: str = Field(max_length=10, description="시장 구분 (KOSPI/KOSDAQ)")
    sector_name: str | None = Field(
        default=None, max_length=50, description="업종명",
    )
    sector_code: str | None = Field(
        default=None, max_length=10, description="Naver 업종코드",
    )
