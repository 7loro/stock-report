"""Anthropic Claude + Tavily 검색 기반 AI 요약 프로바이더"""

import logging

from screening.analysis.ai_provider.base import (
    AIProvider,
    AISummaryResult,
    _build_prompt,
    _parse_response,
)

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-sonnet-4-5-20250929"


class ClaudeProvider(AIProvider):
    """Tavily 웹검색 → Claude 요약 (2단계)"""

    def __init__(
        self,
        api_key: str,
        tavily_key: str,
        model: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._tavily_key = tavily_key
        self._model = model or _DEFAULT_MODEL

    async def search_and_summarize(
        self,
        sector_name: str,
        date: str,
        top_stocks: list[dict],
        avg_change: float,
    ) -> AISummaryResult:
        # ① Tavily 웹검색 (URL 정보 포함)
        search_context, first_url, first_title = (
            await self._search_tavily(sector_name, date, top_stocks)
        )

        # ② Claude 요약
        prompt = _build_prompt(sector_name, date, top_stocks, avg_change)
        prompt += f"\n\n참고할 최신 뉴스:\n{search_context}"

        logger.info(
            "[Claude 요청] 업종=%s | 모델=%s\n  프롬프트: %s",
            sector_name, self._model, prompt[:300],
        )

        from anthropic import Anthropic

        client = Anthropic(api_key=self._api_key)

        response = client.messages.create(
            model=self._model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )

        # 토큰 사용량 로깅
        usage = response.usage
        if usage:
            logger.info(
                "[Claude 토큰] 업종=%s | "
                "입력=%s, 출력=%s, 합계=%s",
                sector_name,
                usage.input_tokens,
                usage.output_tokens,
                usage.input_tokens + usage.output_tokens,
            )

        text = response.content[0].text if response.content else ""

        if not text:
            logger.warning(
                "Claude 응답 비어있음: %s", sector_name,
            )
            return AISummaryResult(summary="요약 불가", keywords=[])

        logger.info(
            "[Claude 응답] 업종=%s\n  %s",
            sector_name, text[:200],
        )
        result = _parse_response(text)

        # Tavily 결과에서 추출한 URL을 fallback으로 사용
        if not result.source_url and first_url:
            result.source_url = first_url
            result.source_title = first_title

        return result

    async def _search_tavily(
        self,
        sector_name: str,
        date: str,
        top_stocks: list[dict],
    ) -> tuple[str, str, str]:
        """Tavily 검색으로 관련 뉴스 수집

        Returns:
            (context_text, first_url, first_title) 튜플
        """
        from tavily import TavilyClient

        client = TavilyClient(api_key=self._tavily_key)

        # 상위 종목명 포함 검색어 구성
        stock_names = " ".join(s["name"] for s in top_stocks[:3])
        query = f"{sector_name} {stock_names} 주가 상승 {date}"

        logger.info("[Tavily 검색] %s", query)

        result = client.search(
            query=query,
            search_depth="basic",
            max_results=5,
        )

        # 검색 결과를 텍스트로 조합 + 첫 번째 URL 추출
        snippets = []
        first_url = ""
        first_title = ""
        for r in result.get("results", []):
            title = r.get("title", "")
            content = r.get("content", "")
            url = r.get("url", "")
            snippets.append(f"- {title}: {content}")
            if not first_url and url:
                first_url = url
                first_title = title

        context = (
            "\n".join(snippets) if snippets else "관련 뉴스 없음"
        )
        return context, first_url, first_title

    async def _call_llm(self, prompt: str) -> str:
        """Claude LLM 호출 (Tavily 없이 순수 텍스트 생성)"""
        from anthropic import Anthropic

        client = Anthropic(api_key=self._api_key)

        response = client.messages.create(
            model=self._model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )

        # 토큰 사용량 로깅
        usage = response.usage
        if usage:
            logger.info(
                "[Claude _call_llm 토큰] "
                "입력=%s, 출력=%s",
                usage.input_tokens,
                usage.output_tokens,
            )

        return response.content[0].text if response.content else ""
