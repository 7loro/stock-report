"""시장 데이터 제공자 추상 인터페이스"""

from abc import ABC, abstractmethod

import pandas as pd


class MarketDataProvider(ABC):
    """시장 데이터 조회 ABC"""

    @abstractmethod
    def get_ticker_list(self, market: str, date: str) -> list[dict]:
        """종목 목록 조회 (market: KOSPI/KOSDAQ)"""
        ...

    @abstractmethod
    def get_ohlcv(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """개별 종목 OHLCV 조회"""
        ...

    @abstractmethod
    def get_all_ohlcv(self, date: str) -> pd.DataFrame:
        """전 종목 당일 OHLCV 조회"""
        ...

    @abstractmethod
    def get_investor_trading(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """투자자별 매매 동향 조회"""
        ...

    @abstractmethod
    def get_program_trading(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """프로그램 매매 동향 조회"""
        ...
