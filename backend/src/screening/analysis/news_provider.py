"""Naver Finance 종목별 뉴스 크롤러"""

import html
import logging
import re
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36"
    ),
    "Referer": "https://finance.naver.com",
}

# 종목별 뉴스 URL (iframe 내부 페이지)
_STOCK_NEWS_URL = (
    "https://finance.naver.com/item/news_news.naver"
)


@dataclass
class NewsItem:
    """크롤링된 뉴스 아이템"""
    title: str      # 뉴스 제목
    url: str        # 뉴스 URL (n.news.naver.com 형식)
    source: str     # 뉴스사 (매경, 한경 등)
    date: str       # 날짜 (YYYY.MM.DD HH:MM)


class NaverStockNewsProvider:
    """Naver Finance 종목별 뉴스 크롤러

    finance.naver.com/item/news_news.naver에서
    종목별 최신 뉴스를 크롤링한다.
    """

    def __init__(self, delay: float = 0.3) -> None:
        self._delay = delay

    def _sleep(self) -> None:
        """rate limit 대기"""
        time.sleep(self._delay)

    def fetch_stock_news(
        self,
        ticker: str,
        max_items: int = 3,
    ) -> list[NewsItem]:
        """특정 종목의 최신 뉴스 크롤링

        Args:
            ticker: 종목코드 (예: "005930")
            max_items: 최대 뉴스 수

        Returns:
            NewsItem 리스트
        """
        try:
            r = requests.get(
                _STOCK_NEWS_URL,
                params={"code": ticker, "page": "1"},
                headers=_NAVER_HEADERS,
                timeout=10,
            )
            self._sleep()
        except Exception:
            logger.warning("뉴스 크롤링 실패: %s", ticker)
            return []

        if r.status_code != 200:
            logger.warning(
                "뉴스 크롤링 HTTP %d: %s",
                r.status_code, ticker,
            )
            return []

        return self._parse_news_page(r.text, max_items)

    def _parse_news_page(
        self,
        page_html: str,
        max_items: int,
    ) -> list[NewsItem]:
        """뉴스 페이지 HTML 파싱

        <table class="type5"> 내 각 <tr> 행에서
        제목(<td class="title"> > <a>),
        뉴스사(<td class="info">),
        날짜(<td class="date">)를 추출한다.
        """
        items: list[NewsItem] = []

        # type5 테이블 영역 추출
        table_match = re.search(
            r'<table[^>]*class="type5"[^>]*>'
            r'(.*?)</table>',
            page_html, re.DOTALL,
        )
        if not table_match:
            return []

        table_html = table_match.group(1)

        # 각 <tr> 행 분리
        rows = re.findall(
            r'<tr[^>]*>(.*?)</tr>',
            table_html, re.DOTALL,
        )

        for row in rows:
            if len(items) >= max_items:
                break

            # title 셀의 <a class="tit"> 링크 추출
            a_match = re.search(
                r'<td[^>]*class="title"[^>]*>'
                r'.*?<a[^>]*href="([^"]*)"[^>]*>'
                r'(.*?)</a>',
                row, re.DOTALL,
            )
            if not a_match:
                continue

            href = a_match.group(1)
            raw_title = a_match.group(2)

            # info, date 셀 추출
            info_match = re.search(
                r'<td[^>]*class="info"[^>]*>'
                r'\s*(.*?)\s*</td>',
                row, re.DOTALL,
            )
            date_match = re.search(
                r'<td[^>]*class="date"[^>]*>'
                r'\s*(.*?)\s*</td>',
                row, re.DOTALL,
            )

            # HTML 태그 제거 + 엔티티 디코딩
            title = html.unescape(
                re.sub(r"<[^>]+>", "", raw_title),
            ).strip()
            source = ""
            if info_match:
                source = re.sub(
                    r"<[^>]+>", "", info_match.group(1),
                ).strip()
            date_str = ""
            if date_match:
                date_str = date_match.group(1).strip()

            if not title:
                continue

            url = self._convert_news_url(href)

            items.append(NewsItem(
                title=title,
                url=url,
                source=source,
                date=date_str,
            ))

        return items

    @staticmethod
    def _convert_news_url(href: str) -> str:
        """Naver Finance 뉴스 URL → n.news.naver.com 형식

        /news_read.naver?...office_id=X&article_id=Y
        → https://n.news.naver.com/mnews/article/X/Y
        """
        office = re.search(r"office_id=(\d+)", href)
        article = re.search(r"article_id=(\d+)", href)

        if office and article:
            return (
                f"https://n.news.naver.com/mnews/article"
                f"/{office.group(1)}/{article.group(1)}"
            )

        # 변환 실패 시 원본 URL 반환
        if href.startswith("/"):
            return f"https://finance.naver.com{href}"
        return href

    def fetch_bulk_news(
        self,
        tickers: list[str],
        max_per_stock: int = 3,
    ) -> dict[str, list[NewsItem]]:
        """여러 종목의 뉴스 일괄 크롤링

        Args:
            tickers: 종목코드 리스트
            max_per_stock: 종목당 최대 뉴스 수

        Returns:
            {ticker: [NewsItem, ...]} 딕셔너리
        """
        result: dict[str, list[NewsItem]] = {}

        for i, ticker in enumerate(tickers):
            news = self.fetch_stock_news(
                ticker, max_per_stock,
            )
            if news:
                result[ticker] = news
                logger.debug(
                    "뉴스 크롤링 [%d/%d] %s: %d건",
                    i + 1, len(tickers), ticker, len(news),
                )
            else:
                logger.debug(
                    "뉴스 크롤링 [%d/%d] %s: 없음",
                    i + 1, len(tickers), ticker,
                )

        logger.info(
            "뉴스 일괄 크롤링 완료: %d/%d 종목에서 뉴스 수집",
            len(result), len(tickers),
        )
        return result
