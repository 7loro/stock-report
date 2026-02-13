"""앱 설정 관리 모듈 (pydantic-settings + TOML 기반)"""

from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class Settings(BaseSettings):
    """TOML 파일 + 환경변수 기반 설정"""

    model_config = SettingsConfigDict(
        toml_file="settings.toml",
    )

    # --- DB ---
    DB_PATH: str = "data/screening.db"

    # --- 텔레그램 ---
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # --- 거래량 조건 ---
    VOLUME_MIN: int = 30_000  # A-1: 최소 거래량
    VOLUME_RATIO: float = 1.5  # A-2, B-1: 전일 대비 배수
    VOLUME_MA_PERIOD: int = 5  # A-3: 이동평균 기간

    # --- 이평선 추세 ---
    TREND_PERIODS: list[int] = [20, 60, 120]  # SMA 기간
    TREND_MIN_COUNT: int = 2  # 연속 상승 최소 횟수

    # --- 골든크로스 ---
    GOLDEN_CROSS_PERIODS: list[int] = [3, 5, 10]  # SMA 기간

    # --- 수급 (합산 순매수 검사 기간) ---
    SUPPLY_DEMAND_PERIODS: list[int] = [2, 5, 20]

    # --- AI 요약 ---
    AI_PROVIDER: str = "openai"       # "openai" | "claude" | "gemini"
    AI_API_KEY: str = ""              # 선택한 프로바이더의 API 키
    AI_MODEL: str = ""                # 모델명 (비어있으면 프로바이더 기본값)
    TAVILY_API_KEY: str = ""          # Claude 사용 시 Tavily 검색 키
    AI_SUMMARY_TOP_N: int = 5         # 상위 N개 업종만 AI 요약
    REPORT_TOP_SECTORS: int = 10      # 리포트 상위 섹터 수 (종목 추출 대상)

    # --- 뉴스 + AI 요약 ---
    NEWS_ENABLED: bool = False       # 뉴스 크롤링 활성화 (기본 비활성)
    NEWS_PER_STOCK: int = 3          # 종목당 크롤링 뉴스 수
    NEWS_CRAWL_DELAY: float = 0.3    # 크롤링 간 딜레이 (초)

    # --- 리포트 ---
    REPORT_BASE_URL: str = ""        # 웹 리포트 기본 URL (예: http://host:8000)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """우선순위: 코드 직접 전달 > 환경변수 > TOML 파일"""
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls),
        )

    @property
    def database_url(self) -> str:
        """SQLite DB URL 반환"""
        db_path = Path(self.DB_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_path}"


# 싱글턴 인스턴스
settings = Settings()
