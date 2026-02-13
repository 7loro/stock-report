"""설정 API 라우터"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from screening.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


class SettingsResponse(BaseModel):
    """설정 응답 모델"""
    VOLUME_MIN: int
    VOLUME_RATIO: float
    VOLUME_MA_PERIOD: int
    TREND_PERIODS: list[int]
    TREND_MIN_COUNT: int
    GOLDEN_CROSS_PERIODS: list[int]
    SUPPLY_DEMAND_PERIODS: list[int]
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: str


class SettingsUpdate(BaseModel):
    """설정 업데이트 모델 (부분 업데이트 가능)"""
    VOLUME_MIN: int | None = None
    VOLUME_RATIO: float | None = None
    VOLUME_MA_PERIOD: int | None = None
    TREND_PERIODS: list[int] | None = None
    TREND_MIN_COUNT: int | None = None
    GOLDEN_CROSS_PERIODS: list[int] | None = None
    SUPPLY_DEMAND_PERIODS: list[int] | None = None
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None


class TelegramTestResponse(BaseModel):
    """텔레그램 테스트 응답"""
    success: bool
    message: str


@router.get("")
def get_settings() -> SettingsResponse:
    """현재 설정 조회"""
    # 토큰은 마스킹 처리
    token = settings.TELEGRAM_BOT_TOKEN
    masked_token = f"{token[:10]}...{token[-4:]}" if len(token) > 14 else token

    return SettingsResponse(
        VOLUME_MIN=settings.VOLUME_MIN,
        VOLUME_RATIO=settings.VOLUME_RATIO,
        VOLUME_MA_PERIOD=settings.VOLUME_MA_PERIOD,
        TREND_PERIODS=settings.TREND_PERIODS,
        TREND_MIN_COUNT=settings.TREND_MIN_COUNT,
        GOLDEN_CROSS_PERIODS=settings.GOLDEN_CROSS_PERIODS,
        SUPPLY_DEMAND_PERIODS=settings.SUPPLY_DEMAND_PERIODS,
        TELEGRAM_BOT_TOKEN=masked_token,
        TELEGRAM_CHAT_ID=settings.TELEGRAM_CHAT_ID,
    )


@router.put("")
def update_settings(update: SettingsUpdate) -> SettingsResponse:
    """설정 업데이트 (런타임 반영)"""
    update_data = update.model_dump(exclude_none=True)

    for key, value in update_data.items():
        if hasattr(settings, key):
            object.__setattr__(settings, key, value)

    logger.info("설정 업데이트: %s", list(update_data.keys()))
    return get_settings()


@router.post("/telegram/test")
async def test_telegram() -> TelegramTestResponse:
    """텔레그램 발송 테스트"""
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return TelegramTestResponse(
            success=False,
            message="텔레그램 설정이 필요합니다 (BOT_TOKEN, CHAT_ID)",
        )

    try:
        from screening.notification.telegram import send_test_message
        await send_test_message()
        return TelegramTestResponse(success=True, message="테스트 메시지 발송 성공")
    except Exception as e:
        logger.error("텔레그램 테스트 실패: %s", e)
        return TelegramTestResponse(success=False, message=f"발송 실패: {e}")
