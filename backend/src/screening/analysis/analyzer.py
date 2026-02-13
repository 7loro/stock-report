"""업종별 상승 종목 집계 + AI 요약 분석기"""

import json
import logging
from datetime import date

import pandas as pd
from sqlmodel import Session, select

from screening.analysis.models import SectorAnalysis, StockAnalysis
from screening.config import settings
from screening.data.cache import CacheManager
from screening.database import engine
from screening.models.stock import Stock

logger = logging.getLogger(__name__)


class SectorAnalyzer:
    """업종별 상승 종목 집계 엔진"""

    def __init__(self, cache: CacheManager | None = None) -> None:
        self._cache = cache or CacheManager()

    def run(
        self,
        target_date: date | None = None,
        *,
        skip_ai: bool = True,
        skip_news: bool = True,
    ) -> tuple[list[SectorAnalysis], list[StockAnalysis]]:
        """업종별 분석 + 종목 TOP 10 통합 실행

        1. 당일 전종목 OHLCV 로드
        2. Stock.sector_code로 업종별 그룹핑
        3. 업종별 집계 + 종목 TOP 10 추출
        4. 상위 항목 AI 요약 (skip_ai=False일 때만)
        4-2. 뉴스 크롤링 + AI 요약 (skip_news=False일 때만)
        5. DB 저장

        Args:
            target_date: 분석 대상 날짜 (기본: 오늘)
            skip_ai: True면 AI 웹검색 요약 건너뜀 (기본: True)
            skip_news: True면 뉴스 크롤링+요약 건너뜀 (기본: True)

        Returns:
            (sectors, stocks) 튜플
        """
        if target_date is None:
            target_date = date.today()

        # ① 전종목 당일 OHLCV 로드
        ohlcv_df = self._cache.fetch_all_ohlcv_latest()
        if ohlcv_df.empty:
            logger.warning("OHLCV 데이터 없음, 분석 중단")
            return [], []

        # ② 종목별 sector_code 매핑 로드
        sector_map = self._load_sector_map()
        if not sector_map:
            logger.warning("업종 매핑 없음, sync-mapping 먼저 실행 필요")
            return [], []

        # OHLCV에 sector 정보 결합
        ohlcv_df["sector_code"] = ohlcv_df.index.map(
            lambda t: sector_map.get(t, {}).get("sector_code"),
        )
        ohlcv_df["sector_name"] = ohlcv_df.index.map(
            lambda t: sector_map.get(t, {}).get("sector_name"),
        )
        ohlcv_df["stock_name"] = ohlcv_df.index.map(
            lambda t: sector_map.get(t, {}).get("name"),
        )

        # sector_code가 없는 종목 제외
        mapped = ohlcv_df.dropna(subset=["sector_code"])
        if mapped.empty:
            logger.warning("업종 매핑된 종목 없음")
            return [], []

        logger.info(
            "전종목 %d개 중 업종 매핑 %d개",
            len(ohlcv_df), len(mapped),
        )

        # ③ 업종별 집계
        sectors = self._aggregate_by_sector(mapped, target_date)
        sectors.sort(key=lambda x: x.avg_change_pct, reverse=True)

        # ③-2 상위 섹터별 상승 종목 추출
        report_n = settings.REPORT_TOP_SECTORS
        top_sector_codes = {
            s.sector_code for s in sectors[:report_n]
        }
        stocks = self._extract_stocks_by_sector(
            mapped, target_date, top_sector_codes,
        )

        # ④ AI 요약 (skip_ai=False일 때만)
        if skip_ai:
            logger.info("AI 요약 스킵 (skip_ai=True)")
        else:
            ai_n = settings.AI_SUMMARY_TOP_N
            self._apply_ai_summaries(
                sectors[:ai_n], stocks,
            )

        # ④-2 뉴스 크롤링 + AI 요약 (skip_news=False일 때만)
        if skip_news:
            logger.info("뉴스 요약 스킵 (skip_news=True)")
        else:
            report_n = settings.REPORT_TOP_SECTORS
            self._apply_news_and_summaries(
                sectors[:report_n], stocks,
            )

        # ⑤ DB 저장
        self._save_results(sectors, stocks, target_date)

        logger.info(
            "분석 완료: %d개 업종, %d개 종목, 날짜=%s",
            len(sectors), len(stocks), target_date,
        )
        return sectors, stocks

    def _load_sector_map(self) -> dict[str, dict]:
        """DB에서 종목별 sector 매핑 로드

        Returns:
            {"005930": {"sector_code": "261", "sector_name": "반도체", "name": "삼성전자"}}
        """
        with Session(engine) as session:
            stocks = session.exec(
                select(Stock).where(Stock.sector_code.is_not(None)),
            ).all()

        return {
            s.ticker: {
                "sector_code": s.sector_code,
                "sector_name": s.sector_name,
                "name": s.name,
            }
            for s in stocks
        }

    def _aggregate_by_sector(
        self,
        df: pd.DataFrame,
        target_date: date,
    ) -> list[SectorAnalysis]:
        """업종별 상승 종목 집계"""
        results = []

        # 등락률 계산 (%)
        if "change_pct" in df.columns:
            # FDR의 ChagesRatio (%) 사용
            df["_change_pct"] = pd.to_numeric(
                df["change_pct"], errors="coerce",
            ).fillna(0)
        else:
            # fallback: 전일 종가 기준 계산
            prev_close = df["close"] - df.get("changes", 0)
            df["_change_pct"] = (
                (df["changes"] / prev_close.replace(0, float("nan"))) * 100
            ).fillna(0)

        # 거래대금: FDR의 Amount 사용, 없으면 종가*거래량
        if "amount" in df.columns:
            df["trading_value"] = pd.to_numeric(
                df["amount"], errors="coerce",
            ).fillna(0)
        else:
            df["trading_value"] = df["close"] * df["volume"]

        # 시가총액 (가중 평균용)
        if "marcap" in df.columns:
            df["_marcap"] = pd.to_numeric(
                df["marcap"], errors="coerce",
            ).fillna(0)
        else:
            df["_marcap"] = 0

        for (sector_code, sector_name), group in df.groupby(
            ["sector_code", "sector_name"],
        ):
            total_count = len(group)
            rising = group[group["_change_pct"] > 0]
            rising_count = len(rising)

            # 시가총액 가중 평균 (네이버 금융 업종 등락률과 동일 방식)
            total_marcap = group["_marcap"].sum()
            if total_marcap > 0:
                avg_change = round(float(
                    (group["_change_pct"] * group["_marcap"]).sum()
                    / total_marcap,
                ), 2)
            else:
                # 시총 데이터 없으면 단순 평균 fallback
                avg_change = round(float(group["_change_pct"].mean()), 2)

            total_value = int(group["trading_value"].sum())

            # 상위 상승 종목 (최대 5개)
            top = group.nlargest(5, "_change_pct")
            top_gainers = [
                {
                    "ticker": idx,
                    "name": row.get("stock_name", idx),
                    "change_pct": round(float(row["_change_pct"]), 2),
                }
                for idx, row in top.iterrows()
            ]

            results.append(SectorAnalysis(
                date=target_date,
                sector_code=str(sector_code),
                sector_name=str(sector_name),
                rising_count=rising_count,
                total_count=total_count,
                avg_change_pct=avg_change,
                total_trading_value=total_value,
                top_gainers=json.dumps(
                    top_gainers, ensure_ascii=False,
                ),
            ))

        return results

    def _extract_stocks_by_sector(
        self,
        df: pd.DataFrame,
        target_date: date,
        sector_codes: set[str],
        top_n: int = 10,
    ) -> list[StockAnalysis]:
        """상위 섹터별 상승 종목 TOP N 추출

        Args:
            df: _aggregate_by_sector에서 전처리된 전종목 DataFrame
            target_date: 분석 대상 날짜
            sector_codes: 추출 대상 섹터 코드 집합
            top_n: 섹터당 최대 종목 수

        Returns:
            섹터별 상승 종목 리스트 (sector_code + rank 기준 정렬)
        """
        # 상승 종목만 필터
        rising = df[df["_change_pct"] > 0]
        if rising.empty:
            return []

        stocks: list[StockAnalysis] = []
        for (sector_code, sector_name), group in rising.groupby(
            ["sector_code", "sector_name"],
        ):
            if str(sector_code) not in sector_codes:
                continue

            top = group.nlargest(top_n, "_change_pct")
            for rank, (ticker, row) in enumerate(
                top.iterrows(), 1,
            ):
                stocks.append(StockAnalysis(
                    date=target_date,
                    sector_code=str(sector_code),
                    sector_name=str(sector_name),
                    rank=rank,
                    ticker=str(ticker),
                    name=str(row.get("stock_name", ticker)),
                    change_pct=round(
                        float(row["_change_pct"]), 2,
                    ),
                    close=int(row["close"]),
                    trading_value=int(row["trading_value"]),
                ))

        logger.info(
            "섹터별 상승 종목 추출: %d개 섹터, 총 %d종목",
            len(sector_codes), len(stocks),
        )
        return stocks

    def _apply_news_and_summaries(
        self,
        sectors: list[SectorAnalysis],
        stocks: list[StockAnalysis],
    ) -> None:
        """뉴스 크롤링 + AI 요약 적용

        1. NaverStockNewsProvider로 상위 섹터 종목 뉴스 일괄 크롤링
        2. 섹터별로 종목+뉴스 묶음 구성
        3. AIProvider.summarize_with_news()로 섹터당 1회 AI 호출
        4. 결과를 StockAnalysis/SectorAnalysis에 저장
        """
        if not settings.AI_API_KEY:
            logger.info("AI API 키 미설정, 뉴스 요약 건너뜀")
            return

        from screening.analysis.news_provider import (
            NaverStockNewsProvider,
        )

        # ① 뉴스 크롤링
        news_provider = NaverStockNewsProvider(
            delay=settings.NEWS_CRAWL_DELAY,
        )
        all_tickers = [s.ticker for s in stocks]
        logger.info(
            "뉴스 크롤링 시작: %d개 종목", len(all_tickers),
        )
        all_news = news_provider.fetch_bulk_news(
            all_tickers,
            max_per_stock=settings.NEWS_PER_STOCK,
        )

        # ② AI 프로바이더 초기화
        try:
            from screening.analysis.ai_provider.base import (
                get_ai_provider,
            )
            provider = get_ai_provider()
        except Exception:
            logger.warning(
                "AI 프로바이더 초기화 실패, 뉴스 요약 건너뜀",
            )
            return

        import asyncio
        import time

        # 종목을 sector_code별로 그룹핑
        stocks_by_sector: dict[str, list[StockAnalysis]] = {}
        for s in stocks:
            stocks_by_sector.setdefault(
                s.sector_code, [],
            ).append(s)

        # ③ 섹터별 AI 호출
        request_idx = 0
        for sector in sectors:
            sector_stocks = stocks_by_sector.get(
                sector.sector_code, [],
            )
            if not sector_stocks:
                continue

            # 종목+뉴스 묶음 구성
            stocks_with_news = []
            for s in sector_stocks:
                news_items = all_news.get(s.ticker, [])
                stocks_with_news.append({
                    "ticker": s.ticker,
                    "name": s.name,
                    "change_pct": s.change_pct,
                    "news": [
                        {
                            "title": n.title,
                            "url": n.url,
                            "source": n.source,
                        }
                        for n in news_items
                    ],
                })

            # 뉴스가 하나도 없는 섹터는 건너뜀
            has_news = any(
                s["news"] for s in stocks_with_news
            )
            if not has_news:
                logger.debug(
                    "뉴스 없음, 요약 스킵: %s",
                    sector.sector_name,
                )
                continue

            try:
                if request_idx > 0:
                    time.sleep(2)

                result = asyncio.run(
                    provider.summarize_with_news(
                        sector_name=sector.sector_name,
                        date=str(sector.date),
                        stocks_with_news=stocks_with_news,
                        avg_change=sector.avg_change_pct,
                    ),
                )

                # ④ 결과 저장
                if result.sector_summary:
                    sector.ai_summary = result.sector_summary

                # 종목별 요약 매칭
                summary_map = {
                    ss.ticker: ss
                    for ss in result.stock_summaries
                }
                for s in sector_stocks:
                    ss = summary_map.get(s.ticker)
                    if ss and ss.summary:
                        s.ai_summary = ss.summary
                        s.source_url = ss.source_url or None
                        s.source_title = (
                            ss.source_title or None
                        )

                request_idx += 1
                logger.info(
                    "뉴스 요약 완료: %s (%d종목)",
                    sector.sector_name,
                    len(result.stock_summaries),
                )
            except Exception:
                logger.warning(
                    "뉴스 요약 실패 (건너뜀): %s",
                    sector.sector_name,
                )

    def _apply_ai_summaries(
        self,
        sectors: list[SectorAnalysis],
        stocks: list[StockAnalysis] | None = None,
    ) -> None:
        """상위 업종 + 종목 TOP 10 AI 요약 적용"""
        if not settings.AI_API_KEY:
            logger.info("AI API 키 미설정, AI 요약 건너뜀")
            return

        try:
            from screening.analysis.ai_provider.base import (
                get_ai_provider,
            )

            provider = get_ai_provider()
        except Exception:
            logger.warning("AI 프로바이더 초기화 실패, 요약 건너뜀")
            return

        import asyncio
        import time

        request_idx = 0

        # 섹터 AI 요약
        for r in sectors:
            try:
                if request_idx > 0:
                    time.sleep(2)

                top_stocks = json.loads(r.top_gainers)
                result = asyncio.run(provider.search_and_summarize(
                    sector_name=r.sector_name,
                    date=str(r.date),
                    top_stocks=top_stocks,
                    avg_change=r.avg_change_pct,
                ))
                r.ai_summary = result.summary
                r.ai_keywords = json.dumps(
                    result.keywords, ensure_ascii=False,
                )
                r.source_url = result.source_url or None
                r.source_title = result.source_title or None
                request_idx += 1
            except Exception:
                logger.warning(
                    "AI 요약 실패 (건너뜀): %s", r.sector_name,
                )

        # 종목 TOP 10 AI 요약
        if stocks:
            for s in stocks:
                try:
                    if request_idx > 0:
                        time.sleep(2)

                    result = asyncio.run(
                        provider.search_and_summarize(
                            sector_name=s.name,
                            date=str(s.date),
                            top_stocks=[{
                                "ticker": s.ticker,
                                "name": s.name,
                                "change_pct": s.change_pct,
                            }],
                            avg_change=s.change_pct,
                        ),
                    )
                    r_summary = result.summary
                    # 1줄로 압축
                    if len(r_summary) > 100:
                        r_summary = r_summary[:97] + "..."
                    s.ai_summary = r_summary
                    s.source_url = result.source_url or None
                    s.source_title = result.source_title or None
                    request_idx += 1
                except Exception:
                    logger.warning(
                        "종목 AI 요약 실패 (건너뜀): %s", s.name,
                    )

    def _save_results(
        self,
        sectors: list[SectorAnalysis],
        stocks: list[StockAnalysis],
        target_date: date,
    ) -> None:
        """분석 결과 DB 저장 (기존 동일 날짜 결과 삭제 후 삽입)"""
        with Session(engine) as session:
            # 기존 섹터 결과 삭제
            existing_sectors = session.exec(
                select(SectorAnalysis).where(
                    SectorAnalysis.date == target_date,
                ),
            ).all()
            for e in existing_sectors:
                session.delete(e)

            # 기존 종목 결과 삭제
            existing_stocks = session.exec(
                select(StockAnalysis).where(
                    StockAnalysis.date == target_date,
                ),
            ).all()
            for e in existing_stocks:
                session.delete(e)

            # 신규 삽입
            for r in sectors:
                session.add(r)
            for s in stocks:
                session.add(s)

            session.commit()

            # 세션 닫힌 후에도 속성 접근 가능하도록 분리
            for r in sectors:
                session.refresh(r)
                session.expunge(r)
            for s in stocks:
                session.refresh(s)
                session.expunge(s)

        logger.info(
            "분석 결과 DB 저장: 섹터 %d건, 종목 %d건 (%s)",
            len(sectors), len(stocks), target_date,
        )

    def get_latest(
        self,
    ) -> tuple[list[SectorAnalysis], list[StockAnalysis]]:
        """최신 분석 결과 조회 (섹터 + 종목)"""
        with Session(engine) as session:
            # 최신 날짜 조회
            latest = session.exec(
                select(SectorAnalysis.date)
                .distinct()
                .order_by(SectorAnalysis.date.desc())
                .limit(1),
            ).first()

            if not latest:
                return [], []

            sectors = list(session.exec(
                select(SectorAnalysis)
                .where(SectorAnalysis.date == latest)
                .order_by(SectorAnalysis.avg_change_pct.desc()),
            ).all())
            for r in sectors:
                session.expunge(r)

            stocks = list(session.exec(
                select(StockAnalysis)
                .where(StockAnalysis.date == latest)
                .order_by(
                    StockAnalysis.sector_code,
                    StockAnalysis.rank,
                ),
            ).all())
            for s in stocks:
                session.expunge(s)

            return sectors, stocks

    def get_by_date(
        self, target_date: date,
    ) -> tuple[list[SectorAnalysis], list[StockAnalysis]]:
        """특정 날짜 분석 결과 조회 (섹터 + 종목)"""
        with Session(engine) as session:
            sectors = list(session.exec(
                select(SectorAnalysis)
                .where(SectorAnalysis.date == target_date)
                .order_by(SectorAnalysis.avg_change_pct.desc()),
            ).all())
            for r in sectors:
                session.expunge(r)

            stocks = list(session.exec(
                select(StockAnalysis)
                .where(StockAnalysis.date == target_date)
                .order_by(
                    StockAnalysis.sector_code,
                    StockAnalysis.rank,
                ),
            ).all())
            for s in stocks:
                session.expunge(s)

            return sectors, stocks
