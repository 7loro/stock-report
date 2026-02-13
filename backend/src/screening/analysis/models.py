"""섹터 분석 데이터 모델 + 종목 분석 모델"""

from datetime import date as date_type, datetime

from sqlmodel import Field, SQLModel


class Sector(SQLModel, table=True):
    """업종 마스터 테이블"""

    __tablename__ = "sectors"

    sector_code: str = Field(
        primary_key=True, max_length=10, description="Naver 업종코드",
    )
    sector_name: str = Field(max_length=50, description="업종명")
    stock_count: int = Field(default=0, description="소속 종목 수")
    updated_at: datetime = Field(
        default_factory=datetime.now, description="마지막 갱신일",
    )


class SectorAnalysis(SQLModel, table=True):
    """업종별 일별 분석 결과 테이블"""

    __tablename__ = "sector_analysis"

    id: int | None = Field(default=None, primary_key=True)
    date: date_type = Field(index=True, description="분석 날짜")
    sector_code: str = Field(max_length=10, description="업종코드")
    sector_name: str = Field(max_length=50, description="업종명")

    # 정량 집계
    rising_count: int = Field(description="상승 종목 수")
    total_count: int = Field(description="전체 종목 수")
    avg_change_pct: float = Field(description="평균 등락률 (%)")
    total_trading_value: int = Field(description="총 거래대금 (원)")
    top_gainers: str = Field(
        default="[]",
        description='상위 종목 JSON: [{"ticker","name","change_pct"}]',
    )

    # AI 요약 (상위 N개 업종만)
    ai_summary: str | None = Field(
        default=None, description="상승 이유 AI 요약",
    )
    ai_keywords: str | None = Field(
        default=None, description='키워드 JSON: ["kw1","kw2"]',
    )
    source_url: str | None = Field(
        default=None, description="참고 뉴스 URL",
    )
    source_title: str | None = Field(
        default=None, description="참고 뉴스 제목",
    )


class StockAnalysis(SQLModel, table=True):
    """개별 종목 일별 분석 결과 테이블 (섹터별 상승 종목)"""

    __tablename__ = "stock_analysis"

    id: int | None = Field(default=None, primary_key=True)
    date: date_type = Field(index=True, description="분석 날짜")
    sector_code: str = Field(
        max_length=10, index=True, description="소속 업종코드",
    )
    sector_name: str | None = Field(
        default=None, description="소속 업종명",
    )
    rank: int = Field(description="섹터 내 순위")
    ticker: str = Field(max_length=10, description="종목코드")
    name: str = Field(max_length=50, description="종목명")
    change_pct: float = Field(description="등락률 (%)")
    close: int = Field(description="종가")
    trading_value: int = Field(description="거래대금 (원)")

    # AI 요약 (추후 개별 종목별 뉴스/공시 연동용)
    ai_summary: str | None = Field(
        default=None, description="상승 이유 AI 요약",
    )
    source_url: str | None = Field(
        default=None, description="참고 뉴스 URL",
    )
    source_title: str | None = Field(
        default=None, description="참고 뉴스 제목",
    )
