"""OpenAI Responses API + web_search 기반 AI 요약 프로바이더"""

import logging

from screening.analysis.ai_provider.base import (
    AIProvider,
    AISummaryResult,
    _build_prompt,
    _parse_response,
)

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gpt-4.1-mini"


class OpenAIProvider(AIProvider):
    """OpenAI web_search 도구를 활용한 요약"""

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
        from openai import OpenAI

        client = OpenAI(api_key=self._api_key)

        prompt = _build_prompt(sector_name, date, top_stocks, avg_change)

        logger.info(
            "[OpenAI 요청] 업종=%s | 모델=%s\n  프롬프트: %s",
            sector_name, self._model, prompt,
        )

        response = client.responses.create(
            model=self._model,
            tools=[{"type": "web_search_preview"}],
            input=prompt,
        )

        # 토큰 사용량 로깅
        usage = response.usage
        if usage:
            logger.info(
                "[OpenAI 토큰] 업종=%s | "
                "입력=%s, 출력=%s, 합계=%s",
                sector_name,
                usage.input_tokens,
                usage.output_tokens,
                usage.input_tokens + usage.output_tokens,
            )

        # 응답 텍스트 추출
        text = ""
        for item in response.output:
            if item.type == "message":
                for content in item.content:
                    if content.type == "output_text":
                        text += content.text

        if not text:
            logger.warning(
                "OpenAI 응답 비어있음: %s", sector_name,
            )
            return AISummaryResult(summary="요약 불가", keywords=[])

        logger.info(
            "[OpenAI 응답] 업종=%s\n  %s",
            sector_name, text[:200],
        )
        result = _parse_response(text)

        # web_search_call 결과에서 첫 번째 URL 추출
        if not result.source_url:
            try:
                for item in response.output:
                    if getattr(item, "type", "") == "web_search_call":
                        results = getattr(item, "results", [])
                        if results:
                            result.source_url = getattr(
                                results[0], "url", "",
                            )
                            result.source_title = getattr(
                                results[0], "title", "",
                            )
                            break
            except (IndexError, AttributeError):
                pass

        return result

    async def _call_llm(self, prompt: str) -> str:
        """OpenAI LLM 호출 (웹검색 도구 없이 순수 텍스트 생성)"""
        from openai import OpenAI

        client = OpenAI(api_key=self._api_key)

        response = client.responses.create(
            model=self._model,
            input=prompt,
        )

        # 토큰 사용량 로깅
        usage = response.usage
        if usage:
            logger.info(
                "[OpenAI _call_llm 토큰] "
                "입력=%s, 출력=%s",
                usage.input_tokens,
                usage.output_tokens,
            )

        text = ""
        for item in response.output:
            if item.type == "message":
                for content in item.content:
                    if content.type == "output_text":
                        text += content.text

        return text
