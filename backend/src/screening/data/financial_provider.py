"""Naver Finance 주요재무정보 크롤러

https://finance.naver.com/item/main.naver?code={ticker} 페이지의
'주요재무정보' 테이블에서 연간/분기 영업이익 등을 파싱한다.
- 멀티레벨 컬럼: Level 0 = '최근 연간 실적' / '최근 분기 실적'
- Level 1 = 기간 (예: '2024.12', '2025.12(E)')
- (E) 마커로 추정치 식별
"""

import logging
import re
import time
from io import StringIO

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_MAIN_URL = "https://finance.naver.com/item/main.naver"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# 주요재무정보 테이블에서 추출할 항목명
_REVENUE_KEY = "매출액"
_OP_INCOME_KEY = "영업이익"
_NET_INCOME_KEY = "당기순이익"


class FinancialProvider:
    """Naver Finance 주요재무정보 크롤러"""

    def __init__(self, delay: float = 0.3) -> None:
        self._delay = delay

    def _sleep(self) -> None:
        time.sleep(self._delay)

    def get_quarterly(self, ticker: str) -> pd.DataFrame:
        """분기별 손익계산서 조회

        반환: index=period("2025/09" 등),
              columns=[revenue, operating_income, net_income, is_estimate]
        """
        annual, quarterly = self._fetch_both(ticker)
        return quarterly

    def get_annual(self, ticker: str) -> pd.DataFrame:
        """연간 손익계산서 조회

        반환: 동일 구조, period="2024" 등
        """
        annual, quarterly = self._fetch_both(ticker)
        return annual

    def get_both(self, ticker: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        """연간 + 분기 동시 조회 (API 호출 1회)

        반환: (quarterly_df, annual_df)
        """
        annual, quarterly = self._fetch_both(ticker)
        return quarterly, annual

    def _fetch_both(
        self, ticker: str,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """네이버 금융 주요재무정보 테이블에서 연간/분기 데이터 한 번에 파싱"""
        empty = self._empty_df()

        try:
            resp = requests.get(
                _MAIN_URL,
                params={"code": ticker},
                headers=_HEADERS,
                timeout=15,
            )
            self._sleep()
            resp.raise_for_status()
        except Exception:
            logger.warning("재무제표 크롤링 실패: %s", ticker)
            return empty, empty

        try:
            tables = pd.read_html(StringIO(resp.text))
        except Exception:
            logger.warning("재무제표 HTML 파싱 실패: %s", ticker)
            return empty, empty

        # '주요재무정보' 테이블 찾기: 3레벨 컬럼 + '영업이익' 행 포함
        fin_table = self._find_financial_table(tables)
        if fin_table is None:
            logger.warning("주요재무정보 테이블 미발견: %s", ticker)
            return empty, empty

        return self._parse_financial_table(fin_table)

    def _find_financial_table(
        self, tables: list[pd.DataFrame],
    ) -> pd.DataFrame | None:
        """테이블 목록에서 주요재무정보 테이블 탐색"""
        for t in tables:
            if t.columns.nlevels < 3:
                continue

            # Level 0에 '최근 연간 실적'이 있는지 확인
            level0 = [str(v) for v in t.columns.get_level_values(0)]
            if not any("최근 연간 실적" in v for v in level0):
                continue

            # '영업이익' 행이 있는지 확인
            first_col_vals = t.iloc[:, 0].astype(str).tolist()
            if any(v.strip() == _OP_INCOME_KEY for v in first_col_vals):
                return t

        return None

    def _parse_financial_table(
        self, df: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """주요재무정보 멀티레벨 테이블을 연간/분기 DataFrame으로 분리

        컬럼 구조 (3레벨):
          Level 0: '주요재무정보' | '최근 연간 실적' | '최근 분기 실적'
          Level 1: '주요재무정보' | '2024.12'        | '2025.12(E)'
          Level 2: '주요재무정보' | 'IFRS연결'       | 'IFRS연결'
        """
        empty = self._empty_df()
        level0 = df.columns.get_level_values(0)
        level1 = df.columns.get_level_values(1)

        # 항목명 컬럼 플래튼
        item_col_idx = 0
        items = df.iloc[:, item_col_idx].astype(str).str.strip()

        # 행 인덱스 매핑
        def _find_row_idx(key: str) -> int | None:
            matches = items[items == key]
            return int(matches.index[0]) if not matches.empty else None

        rev_idx = _find_row_idx(_REVENUE_KEY)
        oi_idx = _find_row_idx(_OP_INCOME_KEY)
        ni_idx = _find_row_idx(_NET_INCOME_KEY)

        if oi_idx is None:
            return empty, empty

        # 연간/분기 컬럼 분리
        annual_records = []
        quarterly_records = []

        for col_pos in range(1, len(df.columns)):
            group = str(level0[col_pos])
            period_raw = str(level1[col_pos])

            if "연간" not in group and "분기" not in group:
                continue

            is_annual = "연간" in group

            # 기간 파싱: "2024.12" → "2024/12", "2025.12(E)" → "2025/12"
            is_estimate = "(E)" in period_raw or "(e)" in period_raw
            period_clean = re.sub(r"\(E\)|\(e\)", "", period_raw).strip()
            # YYYY.MM → YYYY/MM
            period_clean = period_clean.replace(".", "/")

            # 연간은 YYYY만 유지 (YYYY/12 → YYYY)
            if is_annual and period_clean.endswith("/12"):
                period_clean = period_clean[:4]

            record = {
                "period": period_clean,
                "revenue": self._parse_cell(df, rev_idx, col_pos),
                "operating_income": self._parse_cell(df, oi_idx, col_pos),
                "net_income": self._parse_cell(df, ni_idx, col_pos),
                "is_estimate": is_estimate,
            }

            if is_annual:
                annual_records.append(record)
            else:
                quarterly_records.append(record)

        annual_df = self._records_to_df(annual_records)
        quarterly_df = self._records_to_df(quarterly_records)

        return annual_df, quarterly_df

    @staticmethod
    def _parse_cell(
        df: pd.DataFrame, row_idx: int | None, col_pos: int,
    ) -> float:
        """테이블 셀 값을 float로 변환 (콤마, NaN 처리)"""
        if row_idx is None:
            return 0.0
        val = df.iloc[row_idx, col_pos]
        if pd.isna(val):
            return 0.0
        val_str = str(val).replace(",", "").strip()
        try:
            return float(val_str)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _records_to_df(records: list[dict]) -> pd.DataFrame:
        """레코드 리스트 → period 인덱스 DataFrame"""
        if not records:
            return FinancialProvider._empty_df()
        result = pd.DataFrame(records).set_index("period")
        return result.sort_index()

    @staticmethod
    def _empty_df() -> pd.DataFrame:
        return pd.DataFrame(
            columns=["revenue", "operating_income", "net_income", "is_estimate"],
        )
