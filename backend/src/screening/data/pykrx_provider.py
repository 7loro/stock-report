"""FinanceDataReader + Naver Finance 기반 시장 데이터 제공자

KRX 웹사이트 API가 OTP 인증 방식으로 변경되어 pykrx 전종목 조회 불가.
- 종목 목록 / OHLCV: FinanceDataReader 사용
- 투자자 매매: Naver Finance 스크래핑
- 프로그램 순매수: (기관 + 외국인) 순매매로 대체
"""

import logging
import time
from io import StringIO

import pandas as pd
import requests
import FinanceDataReader as fdr

from screening.data.base import MarketDataProvider

logger = logging.getLogger(__name__)

_NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36"
    ),
}


class PykrxProvider(MarketDataProvider):
    """FDR + Naver Finance 기반 데이터 조회 (클래스명 호환성 유지)"""

    def __init__(self, delay: float = 0.3) -> None:
        self._delay = delay

    def _sleep(self) -> None:
        """rate limit 대기"""
        time.sleep(self._delay)

    def get_ticker_list(self, market: str, date: str) -> list[dict]:
        """종목 목록 조회 (FDR StockListing)"""
        df = fdr.StockListing(market)
        self._sleep()

        if df.empty:
            return []

        return [
            {
                "ticker": row["Code"],
                "name": row["Name"],
                "market": market,
            }
            for _, row in df.iterrows()
        ]

    def get_ohlcv(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """개별 종목 OHLCV 조회 (FDR DataReader)"""
        # YYYYMMDD → YYYY-MM-DD 변환
        s = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
        e = f"{end[:4]}-{end[4:6]}-{end[6:8]}"

        df = fdr.DataReader(ticker, s, e)
        self._sleep()

        if df.empty:
            return pd.DataFrame(
                columns=["open", "high", "low", "close", "volume"],
            )

        df = df.rename(columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        })
        cols = ["open", "high", "low", "close", "volume"]
        df = df[[c for c in cols if c in df.columns]]
        df.index.name = "date"
        return df

    def get_all_ohlcv(self, date: str) -> pd.DataFrame:
        """전 종목 당일 OHLCV 조회 (FDR StockListing에 포함)"""
        frames = []
        for market in ["KOSPI", "KOSDAQ"]:
            df = fdr.StockListing(market)
            self._sleep()

            if df.empty:
                continue

            result = pd.DataFrame({
                "open": pd.to_numeric(df["Open"], errors="coerce").values,
                "high": pd.to_numeric(df["High"], errors="coerce").values,
                "low": pd.to_numeric(df["Low"], errors="coerce").values,
                "close": pd.to_numeric(df["Close"], errors="coerce").values,
                "volume": pd.to_numeric(df["Volume"], errors="coerce").values,
                "changes": pd.to_numeric(df["Changes"], errors="coerce").values,
                "change_pct": pd.to_numeric(
                    df.get("ChagesRatio", 0), errors="coerce",
                ).values,
                "amount": pd.to_numeric(
                    df.get("Amount", 0), errors="coerce",
                ).values,
                "marcap": pd.to_numeric(df.get("Marcap", 0), errors="coerce").values,
                "market": market,
            }, index=df["Code"].values)
            result.index.name = "ticker"
            frames.append(result)

        if not frames:
            return pd.DataFrame()

        return pd.concat(frames)

    def get_investor_trading(
        self,
        ticker: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """투자자별 순매매 조회 (Naver Finance 스크래핑)"""
        return self._fetch_naver_investor(ticker, start, end)

    def get_program_trading(
        self,
        ticker: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """프로그램 순매수 조회 (기관+외국인으로 대체)"""
        inv_df = self._fetch_naver_investor(ticker, start, end)
        if inv_df.empty:
            return pd.DataFrame(columns=["program_net_buy"])

        # 프로그램 순매수 ≈ 기관 + 외국인 (실제 프로그램 데이터 미제공)
        prog = inv_df[["institution", "foreign"]].sum(axis=1)
        result = pd.DataFrame({"program_net_buy": prog}, index=inv_df.index)
        result.index.name = "date"
        return result

    def _find_investor_table(self, tables: list[pd.DataFrame]) -> pd.DataFrame | None:
        """Naver Finance 테이블 목록에서 투자자 데이터 테이블 탐색"""
        for t in tables:
            if t.shape[1] != 9:
                continue
            flat = t.dropna(how="all")
            if len(flat) < 2:
                continue
            # 첫 번째 유효 행에 날짜 형식(YYYY.MM.DD) 포함 여부 확인
            first_val = str(flat.iloc[1, 0]) if len(flat) > 1 else ""
            if "." in first_val and len(first_val) == 10:
                return t
        return None

    def _fetch_naver_investor(
        self,
        ticker: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """Naver Finance에서 기관/외국인 순매매량 스크래핑"""
        from datetime import datetime

        start_dt = datetime.strptime(start, "%Y%m%d")
        end_dt = datetime.strptime(end, "%Y%m%d")

        all_rows = []
        # 최대 5페이지 (약 150일치)
        for page in range(1, 6):
            try:
                url = "https://finance.naver.com/item/frgn.naver"
                params = {"code": ticker, "page": page}
                r = requests.get(
                    url, params=params,
                    headers=_NAVER_HEADERS, timeout=10,
                )
                self._sleep()

                tables = pd.read_html(StringIO(r.text))
                raw_df = self._find_investor_table(tables)
                if raw_df is None:
                    break

                df = raw_df.dropna(how="all").copy()
                if df.empty or len(df) < 2:
                    break

                # 멀티인덱스 컬럼 → 단순 컬럼
                df.columns = [
                    "date", "close", "change", "change_rate",
                    "volume", "institution", "foreign",
                    "foreign_holdings", "foreign_rate",
                ]

                # 날짜 파싱 및 필터링
                df["date"] = pd.to_datetime(
                    df["date"], format="%Y.%m.%d", errors="coerce",
                )
                df = df.dropna(subset=["date"])
                df = df[
                    (df["date"] >= start_dt) & (df["date"] <= end_dt)
                ]

                if df.empty:
                    # 범위 이전 데이터에 도달 → 중단
                    raw_clean = raw_df.dropna(how="all")
                    earliest = pd.to_datetime(
                        raw_clean.iloc[-1:][raw_clean.columns[0]],
                        format="%Y.%m.%d",
                        errors="coerce",
                    )
                    if not earliest.empty and earliest.iloc[0] < start_dt:
                        break
                    continue

                all_rows.append(df)

                # 가장 오래된 날짜가 start 이전이면 중단
                if df["date"].min() <= start_dt:
                    break

            except Exception:
                logger.warning("Naver 투자자 데이터 조회 실패: %s page=%d", ticker, page)
                break

        if not all_rows:
            return pd.DataFrame(
                columns=["individual", "foreign", "institution", "program_net_buy"],
            )

        merged = pd.concat(all_rows, ignore_index=True)
        merged = merged.sort_values("date").drop_duplicates(subset=["date"])
        merged = merged.set_index("date")

        # 숫자 변환
        for col in ["institution", "foreign"]:
            merged[col] = pd.to_numeric(
                merged[col].astype(str).str.replace(",", ""),
                errors="coerce",
            ).fillna(0)

        # 개인 = -(기관 + 외국인) 으로 추정
        merged["individual"] = -(merged["institution"] + merged["foreign"])
        # 프로그램 순매수 ≈ 기관 + 외국인
        merged["program_net_buy"] = merged["institution"] + merged["foreign"]

        result = merged[
            ["individual", "foreign", "institution", "program_net_buy"]
        ]
        result.index.name = "date"
        return result
