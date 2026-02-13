"""Naver Finance 업종별 시세 크롤링 + 업종-종목 매핑"""

import logging
import re
import time
from datetime import datetime

import requests
from sqlmodel import Session, select

from screening.analysis.models import Sector
from screening.database import engine
from screening.models.stock import Stock

logger = logging.getLogger(__name__)

_NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36"
    ),
}

# 업종별 소속 종목 URL
_SECTOR_DETAIL_URL = (
    "https://finance.naver.com/sise/sise_group_detail.naver"
)

# 네이버 금융 업종 고정 목록 (code, name)
# https://finance.naver.com/sise/sise_group.naver?type=upjong
SECTOR_LIST: list[tuple[str, str]] = [
    ("334", "무역회사와판매업체"),
    ("276", "복합기업"),
    ("278", "반도체와반도체장비"),
    ("315", "손해보험"),
    ("283", "전기제품"),
    ("301", "은행"),
    ("333", "무선통신서비스"),
    ("272", "화학"),
    ("321", "증권"),
    ("319", "기타금융"),
    ("298", "가정용기기와용품"),
    ("339", "다각화된소비자서비스"),
    ("322", "비철금속"),
    ("330", "생명보험"),
    ("268", "식품"),
    ("336", "다각화된통신서비스"),
    ("267", "IT서비스"),
    ("324", "상업서비스와공급품"),
    ("303", "가구"),
    ("304", "철강"),
    ("323", "해운사"),
    ("309", "음료"),
    ("282", "전자장비와기기"),
    ("308", "인터넷과카탈로그소매"),
    ("297", "가정용품"),
    ("313", "석유와가스"),
    ("326", "항공화물운송과물류"),
    ("284", "우주항공과국방"),
    ("312", "가스유틸리티"),
    ("292", "핸드셋"),
    ("291", "조선"),
    ("25", "기타"),
    ("310", "광고"),
    ("265", "판매업체"),
    ("294", "통신장비"),
    ("337", "카드"),
    ("286", "생물공학"),
    ("305", "항공사"),
    ("296", "운송인프라"),
    ("280", "부동산"),
    ("279", "건설"),
    ("273", "자동차"),
    ("269", "디스플레이장비및부품"),
    ("275", "담배"),
    ("332", "문구류"),
    ("274", "섬유,의류,신발,호화품"),
    ("299", "기계"),
    ("281", "건강관리장비와용품"),
    ("290", "교육서비스"),
    ("261", "제약"),
    ("320", "건축제품"),
    ("318", "종이와목재"),
    ("270", "자동차부품"),
    ("331", "복합유틸리티"),
    ("266", "화장품"),
    ("287", "소프트웨어"),
    ("327", "디스플레이패널"),
    ("306", "전기장비"),
    ("293", "컴퓨터와주변기기"),
    ("262", "생명과학도구및서비스"),
    ("317", "호텔,레스토랑,레저"),
    ("295", "에너지장비및서비스"),
    ("285", "방송과엔터테인먼트"),
    ("311", "포장재"),
    ("288", "건강관리기술"),
    ("316", "건강관리업체및서비스"),
    ("289", "건축자재"),
    ("302", "식품과기본식료품소매"),
    ("328", "전문소매"),
    ("263", "게임엔터테인먼트"),
    ("300", "양방향미디어와서비스"),
    ("277", "창업투자"),
    ("338", "사무용전자제품"),
    ("314", "출판"),
    ("329", "도로와철도운송"),
    ("325", "전기유틸리티"),
    ("271", "레저용장비와제품"),
    ("264", "백화점과일반상점"),
    ("307", "전자제품"),
]


class NaverSectorProvider:
    """Naver Finance 업종 데이터 크롤링"""

    def __init__(self, delay: float = 0.3) -> None:
        self._delay = delay

    def _sleep(self) -> None:
        """rate limit 대기"""
        time.sleep(self._delay)

    @staticmethod
    def get_sector_list_static() -> list[dict]:
        """고정 업종 목록 반환 (크롤링 없음)

        Returns:
            [{"sector_code": "261", "sector_name": "제약"}, ...]
        """
        return [
            {"sector_code": code, "sector_name": name}
            for code, name in SECTOR_LIST
        ]

    def fetch_sector_list(self) -> list[dict]:
        """업종 목록 반환 (고정 리스트 기반, 크롤링 없음)

        Returns:
            [{"sector_code": "261", "sector_name": "제약"}, ...]
        """
        sectors = self.get_sector_list_static()
        logger.info("업종 목록 %d개 (고정 리스트)", len(sectors))
        return sectors

    def fetch_sector_stocks(self, sector_code: str) -> list[str]:
        """특정 업종의 소속 종목 ticker 목록 조회

        Returns:
            ["005930", "000660", ...]
        """
        r = requests.get(
            _SECTOR_DETAIL_URL,
            params={"type": "upjong", "no": sector_code},
            headers=_NAVER_HEADERS,
            timeout=10,
        )
        self._sleep()

        # 종목 코드 추출: /item/main.naver?code=005930
        tickers = re.findall(
            r"item/main\.naver\?code=(\d{6})",
            r.text,
        )
        # 중복 제거 (순서 유지)
        seen = set()
        unique = []
        for t in tickers:
            if t not in seen:
                seen.add(t)
                unique.append(t)

        return unique

    def sync_sector_mapping(self, max_workers: int = 10) -> dict:
        """업종-종목 매핑을 DB에 동기화 (병렬 크롤링)

        Args:
            max_workers: 동시 크롤링 스레드 수

        Returns:
            {"sectors": 갱신 업종 수, "stocks_updated": 갱신 종목 수}
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        sectors = self.fetch_sector_list()
        if not sectors:
            logger.warning("업종 목록이 비어있음, 동기화 중단")
            return {"sectors": 0, "stocks_updated": 0}

        # 병렬로 모든 업종의 소속 종목 크롤링
        sector_stocks: dict[str, list[str]] = {}

        def _fetch(sector_code: str) -> tuple[str, list[str]]:
            try:
                tickers = self.fetch_sector_stocks(sector_code)
                return sector_code, tickers
            except Exception:
                logger.exception("업종 %s 종목 조회 실패", sector_code)
                return sector_code, []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_fetch, s["sector_code"]): s
                for s in sectors
            }
            for future in as_completed(futures):
                code, tickers = future.result()
                sector_stocks[code] = tickers

        # DB 일괄 저장
        now = datetime.now()
        stocks_updated = 0

        with Session(engine) as session:
            for s in sectors:
                code = s["sector_code"]
                name = s["sector_name"]
                tickers = sector_stocks.get(code, [])

                # Sector 마스터 upsert
                existing = session.get(Sector, code)
                if existing:
                    existing.sector_name = name
                    existing.stock_count = len(tickers)
                    existing.updated_at = now
                else:
                    session.add(Sector(
                        sector_code=code,
                        sector_name=name,
                        stock_count=len(tickers),
                        updated_at=now,
                    ))

                # Stock 테이블에 sector_code/name 업데이트
                for ticker in tickers:
                    stock = session.get(Stock, ticker)
                    if stock:
                        stock.sector_code = code
                        stock.sector_name = name
                        stocks_updated += 1

                logger.info(
                    "업종 [%s] %s: %d종목",
                    code, name, len(tickers),
                )

            session.commit()

        logger.info(
            "업종 매핑 동기화 완료: %d개 업종, %d개 종목 갱신",
            len(sectors), stocks_updated,
        )
        return {"sectors": len(sectors), "stocks_updated": stocks_updated}

    def get_all_sectors(self) -> list[Sector]:
        """DB에서 전체 업종 마스터 조회"""
        with Session(engine) as session:
            return list(session.exec(
                select(Sector).order_by(Sector.sector_name),
            ).all())
