# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

한국 주식 시장 자동 스크리닝 시스템. FastAPI + SQLModel 기반 백엔드로, 3단계 필터링 파이프라인(가격/거래량 → 기술적 분석 → 수급)을 통해 종목을 선별하고 텔레그램으로 알림을 보낸다.

## 개발 명령어

```bash
# 의존성 설치 (uv 패키지 매니저 사용)
uv sync

# 서버 실행 (개발)
uv run uvicorn screening.main:app --reload --host 0.0.0.0 --port 8000

# 전체 테스트
uv run pytest tests/ -v

# 단위 테스트 (엔진 조건)
uv run pytest tests/test_engine.py -v

# 통합 테스트 (fixture 기반, 로그 확인 시 --log-cli-level=INFO 추가)
uv run pytest tests/test_screener_integration.py -v --log-cli-level=INFO

# 단일 테스트 실행
uv run pytest tests/test_engine.py::TestPriceCondition::test_pass_양봉_상승 -v

# 의존성 추가
uv add <패키지명>

# 테스트 fixture 생성 (실제 API 호출, 최초 1회 또는 데이터 갱신 시)
uv run python -m tests.generate_fixtures
```

## 아키텍처

### 3단계 스크리닝 파이프라인 (`src/screening/engine/`)

```
1차 필터 (전종목 벡터 연산)
  P-1(상승) + P-2(양봉) + 최소 거래량 → 후보 종목
      ↓
2차 필터 (개별 종목 기술적 분석)
  가격(P) AND 거래량(A/B그룹) AND 추세(T-1~4: SMA) AND 골든크로스(G-1~3) → 기술 통과 종목
      ↓
3차 필터 (수급 분석)
  S-1(프로그램 순매수>0) AND S-2(개인 순매도) → 최종 결과 → DB 저장 + 텔레그램 알림
```

### 레이어 구조

- **`api/`** — FastAPI 라우터 (`/api/screening`, `/api/stocks`, `/api/settings`)
- **`engine/`** — 스크리닝 조건 평가기. `ScreeningCondition` ABC를 상속한 조건 클래스들 (price, volume, trend, golden_cross, supply_demand). `screener.py`가 3단계 통합 엔진
- **`data/`** — 데이터 제공 레이어. `MarketDataProvider` ABC → `PykrxProvider` (FDR + Naver Finance). `CacheManager`가 DB 캐싱 관리
- **`models/`** — SQLModel 데이터 모델 (stock, ohlcv, investor, screening_result)
- **`scheduler/`** — APScheduler 정시 작업 (평일 18:00 데이터 수집, 18:30 스크리닝)
- **`notification/`** — 텔레그램 알림

### 데이터 흐름

API 호출 → CacheManager → DB 조회 → 없으면 FDR/Naver API → DB 저장 후 반환

### 설정 관리

`config.py`의 Pydantic `BaseSettings`가 `.env` 파일에서 로드. 런타임에 `/api/settings` PUT으로 변경 가능. 조건 파라미터(거래량 임계값, SMA 기간 등)는 모두 설정으로 관리.

### 테스트 인프라

- `FileBackedProvider`: Parquet fixture 파일 기반 데이터 제공자 (API 호출 없이 테스트)
- `tests/fixtures/`: `stock_list.json`, `all_ohlcv.parquet`, `ohlcv/{ticker}.parquet`, `investor/{ticker}.parquet`
- `conftest.py`에서 fixture 세팅

## 주요 의존성

- **FastAPI + Uvicorn**: 웹 서버
- **SQLModel**: ORM (SQLAlchemy + Pydantic, SQLite 사용)
- **pandas**: 시계열 데이터 처리 및 벡터 연산
- **FinanceDataReader + pykrx**: 한국 주식 OHLCV 데이터 소스
- **lxml + html5lib**: Naver Finance 투자자 데이터 스크래핑
- **APScheduler**: 정시 자동 실행
- **python-telegram-bot**: 결과 알림
- **pydantic-settings**: 환경 변수 기반 설정

## 코드 규칙

- Python 3.12+, 타입 힌팅 사용
- 비동기 테스트: `pytest-asyncio` (asyncio_mode = "auto")
- 새 스크리닝 조건 추가 시 `ScreeningCondition` ABC를 상속하고 `evaluate()` 메서드 구현
