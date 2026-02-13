"""Google Gemini + Google Search grounding 기반 AI 요약 프로바이더"""

import logging

from screening.analysis.ai_provider.base import (
    AIProvider,
    AISummaryResult,
    _build_prompt,
    _parse_response,
)

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiProvider(AIProvider):
    """Gemini Google Search grounding 내장 활용"""

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model or _DEFAULT_MODEL

    async def search_and_summarize(
        self,
        sector_name: str,
        date: str,
        top_stocks: list[dict],
        avg_change: float,
    ) -> AISummaryResult:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self._api_key)

        prompt = _build_prompt(sector_name, date, top_stocks, avg_change)

        logger.info(
            "[Gemini 요청] 업종=%s | 모델=%s\n  프롬프트: %s",
            sector_name, self._model, prompt,
        )

        # Google Search grounding 도구 설정
        search_tool = types.Tool(
            google_search=types.GoogleSearch(),
        )

        response = client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[search_tool],
            ),
        )

        # 토큰 사용량 로깅
        usage = response.usage_metadata
        if usage:
            logger.info(
                "[Gemini 토큰] 업종=%s | "
                "입력=%s, 출력=%s, 합계=%s",
                sector_name,
                usage.prompt_token_count,
                usage.candidates_token_count,
                usage.total_token_count,
            )

        text = response.text if response.text else ""

        if not text:
            logger.warning(
                "Gemini 응답 비어있음: %s", sector_name,
            )
            return AISummaryResult(summary="요약 불가", keywords=[])

        logger.info(
            "[Gemini 응답] 업종=%s\n  %s",
            sector_name, text[:200],
        )
        result = _parse_response(text)

        # grounding_metadata에서 첫 번째 URL/title 추출
        try:
            candidate = response.candidates[0]
            grounding = getattr(candidate, "grounding_metadata", None)
            if grounding:
                chunks = getattr(grounding, "grounding_chunks", None)
                if chunks:
                    web = getattr(chunks[0], "web", None)
                    if web:
                        result.source_url = (
                            result.source_url
                            or getattr(web, "uri", "")
                        )
                        result.source_title = (
                            result.source_title
                            or getattr(web, "title", "")
                        )
        except (IndexError, AttributeError):
            pass

        return result

    async def _call_llm(self, prompt: str) -> str:
        """Gemini LLM 호출 (grounding 없이 순수 텍스트 생성)"""
        from google import genai

        client = genai.Client(api_key=self._api_key)

        response = client.models.generate_content(
            model=self._model,
            contents=prompt,
        )

        # 토큰 사용량 로깅
        usage = response.usage_metadata
        if usage:
            logger.info(
                "[Gemini _call_llm 토큰] "
                "입력=%s, 출력=%s",
                usage.prompt_token_count,
                usage.candidates_token_count,
            )

        return response.text if response.text else ""
