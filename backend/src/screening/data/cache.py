"""DB 캐시 매니저 - API 호출 최소화를 위한 캐싱 레이어"""

import logging
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func as sa_func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, select

from screening.database import engine
from screening.models.ohlcv import DailyOHLCV
from screening.models.financial import FinancialStatement
from screening.models.investor import InvestorTrading
from screening.models.stock import Stock
from screening.data.financial_provider import FinancialProvider
from screening.data.pykrx_provider import PykrxProvider

logger = logging.getLogger(__name__)

# SQLite 변수 제한 고려 벌크 배치 크기
_BATCH_SIZE = 500


class CacheManager:
    """DB 캐시를 활용한 데이터 관리"""

    def __init__(
        self,
        provider: PykrxProvider | None = None,
        financial_provider: FinancialProvider | None = None,
        bypass_db: bool = False,
    ) -> None:
        self._provider = provider or PykrxProvider()
        self._financial_provider = financial_provider or FinancialProvider()
        self._bypass_db = bypass_db

    def ensure_stock_list(self, date_str: str) -> list[Stock]:
        """종목 마스터를 DB에 저장/갱신"""
        stocks = []
        for market in ["KOSPI", "KOSDAQ"]:
            ticker_list = self._provider.get_ticker_list(market, date_str)
            for item in ticker_list:
                stock = Stock(
                    ticker=item["ticker"],
                    name=item["name"],
                    market=item["market"],
                )
                stocks.append(stock)

        if self._bypass_db:
            logger.info("종목 마스터 조회 완료 (bypass_db): %d건", len(stocks))
            return stocks

        # 벌크 upsert
        records = [
            {"ticker": s.ticker, "name": s.name, "market": s.market}
            for s in stocks
        ]
        if records:
            table = Stock.__table__
            with Session(engine) as session:
                for i in range(0, len(records), _BATCH_SIZE):
                    batch = records[i:i + _BATCH_SIZE]
                    stmt = sqlite_insert(table).values(batch)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["ticker"],
                        set_={
                            "name": stmt.excluded.name,
                            "market": stmt.excluded.market,
                        },
                    )
                    session.execute(stmt)
                session.commit()

        logger.info("종목 마스터 갱신 완료: %d건", len(stocks))

        # DB에서 다시 조회하여 sector_name 등 기존 데이터 포함된 레코드 반환
        with Session(engine) as session:
            db_stocks = list(session.exec(select(Stock)).all())
            for s in db_stocks:
                session.expunge(s)
            return db_stocks

    def fetch_all_ohlcv_latest(self) -> pd.DataFrame:
        """전 종목 최신 OHLCV 조회 (FDR StockListing, DB 저장 없이 반환)

        FDR StockListing은 최신 거래일 데이터만 반환.
        1차 필터용으로 DB 저장 없이 직접 사용.
        """
        df = self._provider.get_all_ohlcv("")
        if not df.empty:
            logger.info("전 종목 최신 OHLCV 조회: %d건", len(df))
        return df

    def find_stale_tickers(
        self,
        tickers: list[str],
        ohlcv_start: date,
        inv_start: date,
        end: date,
    ) -> set[str]:
        """캐시 갱신이 필요한 종목 코드 반환 (OHLCV or 투자자 부족)

        배치 쿼리 2개로 전종목 캐시 상태를 한 번에 확인한다.
        배치 사전 캐싱 용도이므로 3일 tolerance 적용 (주말·공휴일 감안).
        당일 데이터 gap은 ensure_ohlcv/ensure_investor_data가 개별 처리.
        """
        threshold = end - timedelta(days=3)
        all_tickers = set(tickers)

        with Session(engine) as session:
            # OHLCV: 종목별 최신 캐시 날짜
            ohlcv_rows = session.execute(
                select(DailyOHLCV.ticker, sa_func.max(DailyOHLCV.date))
                .where(DailyOHLCV.date >= ohlcv_start)
                .group_by(DailyOHLCV.ticker)
            ).all()
            ohlcv_fresh = {r[0] for r in ohlcv_rows if r[1] >= threshold}

            # 투자자: 종목별 최신 캐시 날짜
            inv_rows = session.execute(
                select(InvestorTrading.ticker, sa_func.max(InvestorTrading.date))
                .where(InvestorTrading.date >= inv_start)
                .group_by(InvestorTrading.ticker)
            ).all()
            inv_fresh = {r[0] for r in inv_rows if r[1] >= threshold}

        # OHLCV와 투자자 둘 다 최신인 종목만 제외
        both_fresh = ohlcv_fresh & inv_fresh
        return all_tickers - both_fresh

    def ensure_ohlcv(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """DB 캐시 우선 조회, 부족한 구간만 API 호출 후 전체 반환"""
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        if self._bypass_db:
            return self._provider.get_ohlcv(ticker, start_str, end_str)

        # DB에서 기존 데이터 조회
        with Session(engine) as session:
            stmt = (
                select(DailyOHLCV)
                .where(DailyOHLCV.ticker == ticker)
                .where(DailyOHLCV.date >= start)
                .where(DailyOHLCV.date <= end)
                .order_by(DailyOHLCV.date)
            )
            existing = session.exec(stmt).all()

        if existing:
            max_cached = max(r.date for r in existing)

            # end 날짜까지 캐시 완료 → 그대로 반환
            if max_cached >= end:
                return self._rows_to_ohlcv_df(existing)

            # 부족한 구간 API 호출 (max_cached < end)
            gap_start = max_cached + timedelta(days=1)
            df = self._provider.get_ohlcv(
                ticker, gap_start.strftime("%Y%m%d"), end_str,
            )
            if not df.empty:
                self._save_ohlcv(ticker, df)

            # 기존 + 신규 합쳐서 반환
            existing_df = self._rows_to_ohlcv_df(existing)
            if not df.empty:
                combined = pd.concat([existing_df, df])
                combined = combined[~combined.index.duplicated(keep="last")]
                return combined.sort_index()

            # API에서 새 데이터 없음 (공휴일/주말 등) → 기존 캐시 반환
            return existing_df

        # 캐시 없음 → 전체 조회
        df = self._provider.get_ohlcv(ticker, start_str, end_str)
        if not df.empty:
            self._save_ohlcv(ticker, df)
            return df

        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    def ensure_investor_data(
        self,
        ticker: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """투자자 데이터 조회 (DB 캐시 우선, 부족 시 API 호출)"""
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        if self._bypass_db:
            return self._provider.get_investor_trading(ticker, start_str, end_str)

        # DB에서 기존 데이터 조회
        with Session(engine) as session:
            stmt = (
                select(InvestorTrading)
                .where(InvestorTrading.ticker == ticker)
                .where(InvestorTrading.date >= start)
                .where(InvestorTrading.date <= end)
                .order_by(InvestorTrading.date)
            )
            existing = session.exec(stmt).all()

        if existing:
            max_cached = max(r.date for r in existing)

            # end 날짜까지 캐시 완료 → 그대로 반환
            if max_cached >= end:
                return self._rows_to_investor_df(existing)

            # 부족한 구간 API 호출 (max_cached < end)
            gap_start = max_cached + timedelta(days=1)
            df = self._provider.get_investor_trading(
                ticker, gap_start.strftime("%Y%m%d"), end_str,
            )
            if not df.empty:
                self._save_investor(ticker, df)

            existing_df = self._rows_to_investor_df(existing)
            if not df.empty:
                combined = pd.concat([existing_df, df])
                combined = combined[~combined.index.duplicated(keep="last")]
                return combined.sort_index()

            # API에서 새 데이터 없음 (공휴일/주말 등) → 기존 캐시 반환
            return existing_df

        # 캐시 없음 → 전체 조회
        df = self._provider.get_investor_trading(ticker, start_str, end_str)
        if not df.empty:
            self._save_investor(ticker, df)
            return df

        return pd.DataFrame(
            columns=["individual", "foreign", "institution", "program_net_buy"],
        )

    def _save_ohlcv(self, ticker: str, df: pd.DataFrame) -> None:
        """OHLCV DataFrame을 DB에 벌크 저장"""
        records = []
        for idx, row in df.iterrows():
            d = idx.date() if hasattr(idx, "date") else idx
            records.append({
                "ticker": ticker,
                "date": d,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
            })

        if not records:
            return

        table = DailyOHLCV.__table__
        with Session(engine) as session:
            for i in range(0, len(records), _BATCH_SIZE):
                batch = records[i:i + _BATCH_SIZE]
                stmt = sqlite_insert(table).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["ticker", "date"],
                    set_={
                        "open": stmt.excluded.open,
                        "high": stmt.excluded.high,
                        "low": stmt.excluded.low,
                        "close": stmt.excluded.close,
                        "volume": stmt.excluded.volume,
                    },
                )
                session.execute(stmt)
            session.commit()

    def _save_investor(self, ticker: str, df: pd.DataFrame) -> None:
        """투자자 DataFrame을 DB에 벌크 저장"""
        records = []
        for idx, row in df.iterrows():
            d = idx.date() if hasattr(idx, "date") else idx
            records.append({
                "ticker": ticker,
                "date": d,
                "individual": float(row.get("individual", 0)),
                "foreign": float(row.get("foreign", 0)),
                "institution": float(row.get("institution", 0)),
                "program_net_buy": float(row.get("program_net_buy", 0)),
            })

        if not records:
            return

        table = InvestorTrading.__table__
        with Session(engine) as session:
            for i in range(0, len(records), _BATCH_SIZE):
                batch = records[i:i + _BATCH_SIZE]
                stmt = sqlite_insert(table).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["ticker", "date"],
                    set_={
                        "individual": stmt.excluded.individual,
                        "foreign": stmt.excluded.foreign,
                        "institution": stmt.excluded.institution,
                        "program_net_buy": stmt.excluded.program_net_buy,
                    },
                )
                session.execute(stmt)
            session.commit()

    def _rows_to_ohlcv_df(self, rows: list[DailyOHLCV]) -> pd.DataFrame:
        """DailyOHLCV 목록 → DataFrame 변환"""
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        data = [
            {
                "date": r.date,
                "open": r.open_price,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
            }
            for r in rows
        ]
        return pd.DataFrame(data).set_index("date")

    def _rows_to_investor_df(self, rows: list[InvestorTrading]) -> pd.DataFrame:
        """InvestorTrading 목록 → DataFrame 변환"""
        if not rows:
            return pd.DataFrame(
                columns=["individual", "foreign", "institution", "program_net_buy"],
            )
        data = [
            {
                "date": r.date,
                "individual": r.individual,
                "foreign": r.foreign_val,
                "institution": r.institution,
                "program_net_buy": r.program_net_buy,
            }
            for r in rows
        ]
        return pd.DataFrame(data).set_index("date")

    # ── 재무제표 캐싱 ──

    _MIN_QUARTERLY = 5   # YoY 비교에 필요한 최소 분기 수
    _MIN_ANNUAL = 2      # 적자전환 비교에 필요한 최소 연간 수

    def ensure_financial_data(
        self,
        ticker: str,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """분기/연간 재무제표 DB 캐시 조회. 없으면 Naver 크롤링 후 저장.

        반환: (quarterly_df, annual_df) — index=period, columns=[revenue, operating_income, ...]
        """
        empty_q = pd.DataFrame(
            columns=["revenue", "operating_income", "net_income", "is_estimate"],
        )
        empty_a = empty_q.copy()

        if self._bypass_db:
            return self._financial_provider.get_both(ticker)

        # DB 캐시 조회
        with Session(engine) as session:
            q_rows = session.exec(
                select(FinancialStatement)
                .where(FinancialStatement.ticker == ticker)
                .where(FinancialStatement.freq == "Q")
                .order_by(FinancialStatement.period)
            ).all()
            a_rows = session.exec(
                select(FinancialStatement)
                .where(FinancialStatement.ticker == ticker)
                .where(FinancialStatement.freq == "Y")
                .order_by(FinancialStatement.period)
            ).all()

        q_hit = len(q_rows) >= self._MIN_QUARTERLY
        a_hit = len(a_rows) >= self._MIN_ANNUAL

        if q_hit and a_hit:
            return (
                self._rows_to_financial_df(q_rows),
                self._rows_to_financial_df(a_rows),
            )

        # 부족하면 크롤링 (API 1회로 연간+분기 동시 조회) → DB 저장
        q_df, a_df = self._financial_provider.get_both(ticker)

        if not q_df.empty:
            self._save_financial(ticker, q_df, "Q")
        if not a_df.empty:
            self._save_financial(ticker, a_df, "Y")

        # DB에 이미 있던 쪽은 기존 캐시 사용
        if q_hit:
            q_df = self._rows_to_financial_df(q_rows)
        if a_hit:
            a_df = self._rows_to_financial_df(a_rows)

        return q_df, a_df

    def _save_financial(
        self, ticker: str, df: pd.DataFrame, freq: str,
    ) -> None:
        """재무제표 DataFrame DB 벌크 저장"""
        records = []
        for period, row in df.iterrows():
            records.append({
                "ticker": ticker,
                "period": str(period),
                "freq": freq,
                "revenue": float(row.get("revenue", 0)),
                "operating_income": float(row.get("operating_income", 0)),
                "net_income": float(row.get("net_income", 0)),
                "is_estimate": bool(row.get("is_estimate", False)),
            })

        if not records:
            return

        table = FinancialStatement.__table__
        with Session(engine) as session:
            for i in range(0, len(records), _BATCH_SIZE):
                batch = records[i:i + _BATCH_SIZE]
                stmt = sqlite_insert(table).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["ticker", "period"],
                    set_={
                        "freq": stmt.excluded.freq,
                        "revenue": stmt.excluded.revenue,
                        "operating_income": stmt.excluded.operating_income,
                        "net_income": stmt.excluded.net_income,
                        "is_estimate": stmt.excluded.is_estimate,
                    },
                )
                session.execute(stmt)
            session.commit()

    @staticmethod
    def _rows_to_financial_df(
        rows: list[FinancialStatement],
    ) -> pd.DataFrame:
        """FinancialStatement 목록 → DataFrame 변환"""
        if not rows:
            return pd.DataFrame(
                columns=["revenue", "operating_income", "net_income", "is_estimate"],
            )
        data = [
            {
                "period": r.period,
                "revenue": r.revenue,
                "operating_income": r.operating_income,
                "net_income": r.net_income,
                "is_estimate": r.is_estimate,
            }
            for r in rows
        ]
        return pd.DataFrame(data).set_index("period")
