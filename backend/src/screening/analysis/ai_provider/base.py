"""AI 요약 프로바이더 추상 인터페이스 + 팩토리"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from screening.config import settings

logger = logging.getLogger(__name__)


@dataclass
class AISummaryResult:
    """AI 요약 결과"""
    summary: str       # 2-3문장 요약
    keywords: list[str]  # 핵심 키워드 5개
    source_url: str = ""    # 참고 뉴스 URL
    source_title: str = ""  # 참고 뉴스 제목


@dataclass
class StockNewsSummary:
    """종목별 뉴스 기반 1줄 요약"""
    ticker: str
    summary: str        # 1줄 요약
    source_url: str     # 대표 뉴스 URL
    source_title: str   # 대표 뉴스 제목


@dataclass
class SectorNewsSummaryResult:
    """섹터별 뉴스 기반 AI 요약 결과"""
    sector_summary: str                            # 섹터 전체 요약 (1-2문장)
    stock_summaries: list[StockNewsSummary] = field(
        default_factory=list,
    )


class AIProvider(ABC):
    """AI 웹검색 요약 추상 인터페이스"""

    @abstractmethod
    async def search_and_summarize(
        self,
        sector_name: str,
        date: str,
        top_stocks: list[dict],
        avg_change: float,
    ) -> AISummaryResult:
        """업종 상승 이유를 웹검색 후 요약

        Args:
            sector_name: 업종명 (예: "반도체와반도체장비")
            date: 분석 날짜 (YYYY-MM-DD)
            top_stocks: 상위 종목 [{ticker, name, change_pct}]
            avg_change: 평균 등락률 (%)

        Returns:
            AISummaryResult (요약 + 키워드)
        """

    async def _call_llm(self, prompt: str) -> str:
        """LLM 호출 (서브클래스에서 오버라이드)

        Args:
            prompt: 전송할 프롬프트

        Returns:
            LLM 응답 텍스트
        """
        raise NotImplementedError

    async def summarize_with_news(
        self,
        sector_name: str,
        date: str,
        stocks_with_news: list[dict],
        avg_change: float,
    ) -> SectorNewsSummaryResult:
        """크롤링된 뉴스 기반 섹터 + 종목 요약

        섹터별 1회 AI 호출로 섹터 요약 + 종목별 1줄 요약을 생성한다.

        Args:
            sector_name: 업종명
            date: 분석 날짜 (YYYY-MM-DD)
            stocks_with_news: 종목+뉴스 리스트
                [{"ticker": "005930", "name": "삼성전자",
                  "change_pct": 3.2,
                  "news": [{"title": "...", "url": "...",
                            "source": "매경"}]}]
            avg_change: 평균 등락률 (%)

        Returns:
            SectorNewsSummaryResult
        """
        prompt = _build_news_prompt(
            sector_name, date, stocks_with_news, avg_change,
        )

        logger.info(
            "[뉴스 요약 요청] 업종=%s, 종목 %d개",
            sector_name, len(stocks_with_news),
        )

        try:
            text = await self._call_llm(prompt)
        except Exception:
            logger.warning(
                "뉴스 기반 AI 요약 실패: %s", sector_name,
            )
            return SectorNewsSummaryResult(sector_summary="")

        return _parse_news_response(text, stocks_with_news)


def _build_prompt(
    sector_name: str,
    date: str,
    top_stocks: list[dict],
    avg_change: float,
) -> str:
    """공통 프롬프트 생성"""
    stocks_str = ", ".join(
        f"{s['name']}(+{s['change_pct']}%)" for s in top_stocks[:5]
    )
    return (
        f"한국 주식 시장에서 {date} 기준으로 "
        f"'{sector_name}' 업종이 평균 {avg_change:+.1f}% 상승했습니다.\n"
        f"주요 상승 종목: {stocks_str}\n\n"
        f"이 업종이 오늘 상승한 이유를 최신 뉴스를 검색하여 "
        f"한국어로 2-3문장으로 요약해주세요.\n"
        f"마지막에 핵심 키워드 5개를 '키워드: 키워드1, 키워드2, ...' "
        f"형태로 추가해주세요.\n"
        f"참고한 뉴스가 있다면 가장 관련도 높은 뉴스 URL 1개를 "
        f"'출처: URL' 형태로 마지막 줄에 포함해주세요."
    )


def _parse_keywords(text: str) -> list[str]:
    """AI 응답에서 키워드 추출"""
    import re

    # '키워드:' 또는 '키워드 :' 패턴 탐색
    match = re.search(r"키워드\s*[:：]\s*(.+)", text)
    if match:
        raw = match.group(1).strip()
        # 쉼표 또는 공백 구분
        keywords = [
            k.strip().strip("#")
            for k in re.split(r"[,，、\s]+", raw)
            if k.strip()
        ]
        return keywords[:5]

    return []


def _parse_source_url(text: str) -> tuple[str, str]:
    """AI 응답에서 출처 URL 추출

    Returns:
        (url, title) 튜플. 없으면 ("", "")
    """
    import re

    # '출처:' 또는 '출처 :' 뒤의 URL 추출
    match = re.search(
        r"출처\s*[:：]\s*(https?://\S+)", text,
    )
    if match:
        return match.group(1).strip(), ""
    return "", ""


def _parse_response(text: str) -> AISummaryResult:
    """AI 응답을 요약 + 키워드 + 출처로 분리"""
    keywords = _parse_keywords(text)
    source_url, source_title = _parse_source_url(text)

    # 키워드/출처 라인 제거한 요약
    import re
    summary = re.sub(r"\n*키워드\s*[:：].+", "", text).strip()
    summary = re.sub(r"\n*출처\s*[:：].+", "", summary).strip()

    return AISummaryResult(
        summary=summary,
        keywords=keywords,
        source_url=source_url,
        source_title=source_title,
    )


def _build_news_prompt(
    sector_name: str,
    date: str,
    stocks_with_news: list[dict],
    avg_change: float,
) -> str:
    """뉴스 기반 요약용 프롬프트 생성"""
    lines = [
        f"한국 주식 시장 {date} 기준, "
        f"'{sector_name}' 업종이 평균 {avg_change:+.1f}% 변동했습니다.",
        "",
        "아래 종목별 최신 뉴스를 바탕으로 요약해주세요.",
        "",
    ]

    for stock in stocks_with_news:
        name = stock["name"]
        ticker = stock["ticker"]
        change = stock.get("change_pct", 0)
        lines.append(
            f"### {name} ({ticker}) {change:+.1f}%",
        )

        news_list = stock.get("news", [])
        if news_list:
            for n in news_list:
                lines.append(
                    f"- [{n['source']}] {n['title']}",
                )
        else:
            lines.append("- (뉴스 없음)")
        lines.append("")

    lines.extend([
        "---",
        "아래 JSON 형식으로만 응답하세요. "
        "다른 텍스트는 포함하지 마세요.",
        "",
        '{"sector_summary": "섹터 전체 상승/하락 이유 1-2문장 요약",'
        ' "stocks": [{"ticker": "005930",'
        ' "summary": "종목별 1줄 요약 (30자 이내)"}]}',
        "",
        "중요 규칙:",
        "- 뉴스에 없는 내용을 추측하지 마시오.",
        "- 뉴스가 없는 종목은 summary를 빈 문자열로 반환.",
        "- 요약은 한국어로 작성.",
    ])

    return "\n".join(lines)


def _parse_news_response(
    text: str,
    stocks_with_news: list[dict],
) -> SectorNewsSummaryResult:
    """뉴스 요약 AI 응답 파싱"""
    # JSON 블록 추출 (```json ... ``` 또는 순수 JSON)
    import re
    json_match = re.search(
        r"```(?:json)?\s*(.*?)```",
        text, re.DOTALL,
    )
    json_str = json_match.group(1) if json_match else text

    try:
        data = json.loads(json_str.strip())
    except json.JSONDecodeError:
        logger.warning("뉴스 요약 JSON 파싱 실패: %s", text[:200])
        return SectorNewsSummaryResult(sector_summary="")

    sector_summary = data.get("sector_summary", "")

    # 종목별 요약 매칭
    stock_map = {
        s["ticker"]: s for s in stocks_with_news
    }
    summaries: list[StockNewsSummary] = []

    for item in data.get("stocks", []):
        ticker = item.get("ticker", "")
        summary = item.get("summary", "")
        if not ticker or not summary:
            continue

        # 대표 뉴스 URL/제목은 해당 종목 첫 뉴스 사용
        stock_data = stock_map.get(ticker, {})
        news_list = stock_data.get("news", [])
        source_url = news_list[0]["url"] if news_list else ""
        source_title = news_list[0]["source"] if news_list else ""

        summaries.append(StockNewsSummary(
            ticker=ticker,
            summary=summary,
            source_url=source_url,
            source_title=source_title,
        ))

    return SectorNewsSummaryResult(
        sector_summary=sector_summary,
        stock_summaries=summaries,
    )


def get_ai_provider() -> AIProvider:
    """설정에 따른 AI 프로바이더 팩토리"""
    provider_type = settings.AI_PROVIDER.lower()

    match provider_type:
        case "openai":
            from screening.analysis.ai_provider.openai_provider import (
                OpenAIProvider,
            )
            return OpenAIProvider(
                api_key=settings.AI_API_KEY,
                model=settings.AI_MODEL or None,
            )

        case "claude":
            from screening.analysis.ai_provider.claude_provider import (
                ClaudeProvider,
            )
            return ClaudeProvider(
                api_key=settings.AI_API_KEY,
                tavily_key=settings.TAVILY_API_KEY,
                model=settings.AI_MODEL or None,
            )

        case "gemini":
            from screening.analysis.ai_provider.gemini_provider import (
                GeminiProvider,
            )
            return GeminiProvider(
                api_key=settings.AI_API_KEY,
                model=settings.AI_MODEL or None,
            )

        case _:
            raise ValueError(
                f"지원하지 않는 AI 프로바이더: {provider_type}. "
                f"openai/claude/gemini 중 선택",
            )
