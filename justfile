# Father Stock Strategy - Justfile
# 한국 주식 자동 스크리닝 + 장 마감 리포트 시스템

# 기본 레시피 (just 입력 시 표시)
default:
    @just --list

# ──────────────────────────────────────
# 백엔드 설치 & 환경
# ──────────────────────────────────────

# 의존성 설치
install:
    cd backend && uv sync

# AI 프로바이더 포함 전체 설치
install-all:
    cd backend && uv sync --extra ai-all

# Gemini AI만 추가 설치
install-gemini:
    cd backend && uv sync --extra ai-gemini

# 의존성 업데이트
upgrade:
    cd backend && uv lock --upgrade && uv sync

# ──────────────────────────────────────
# 서버 실행
# ──────────────────────────────────────

# 개발 서버 실행 (auto-reload, 포트 8000)
dev:
    cd backend && uv run uvicorn screening.main:app --reload --host 0.0.0.0 --port 8000

# 프로덕션 서버 실행
serve:
    cd backend && uv run uvicorn screening.main:app --host 0.0.0.0 --port 8000

# 서버 헬스체크
health:
    @curl -s http://localhost:8000/api/health | python3 -m json.tool

# ──────────────────────────────────────
# 테스트
# ──────────────────────────────────────

# 전체 테스트 실행
test:
    cd backend && uv run pytest tests/ -v

# 엔진 조건 단위 테스트
test-engine:
    cd backend && uv run pytest tests/test_engine.py -v

# 전략 테스트
test-strategy:
    cd backend && uv run pytest tests/test_strategy.py -v

# 통합 테스트 (로그 포함)
test-integration:
    cd backend && uv run pytest tests/test_screener_integration.py -v --log-cli-level=INFO

# 특정 테스트 실행 (예: just test-one test_engine.py::TestPriceCondition)
test-one target:
    cd backend && uv run pytest tests/{{ target }} -v

# 테스트 Fixture 증분 갱신
fixtures:
    cd backend && uv run python -m tests.generate_fixtures

# 테스트 Fixture 전체 재수집
fixtures-full:
    cd backend && uv run python -m tests.generate_fixtures --full

# ──────────────────────────────────────
# 스크리닝
# ──────────────────────────────────────

# 스크리닝 E2E: 데이터 캐싱
screen-cache:
    cd backend && uv run python -m scripts.test_screening --step cache

# 스크리닝 E2E: 실행 (오늘 날짜)
screen-run:
    cd backend && uv run python -m scripts.test_screening --step run

# 스크리닝 E2E: 특정 날짜 실행 (예: just screen-date 2026-02-13)
screen-date date:
    cd backend && uv run python -m scripts.test_screening --step run --date {{ date }}

# 스크리닝 E2E: 캐싱 + 실행 전체
screen-all:
    cd backend && uv run python -m scripts.test_screening --step all

# API로 스크리닝 수동 실행
screen-api strategy="DEFAULT":
    @curl -s -X POST "http://localhost:8000/api/screening/run?strategy={{ strategy }}" | python3 -m json.tool

# 최신 스크리닝 결과 조회
screen-latest:
    @curl -s http://localhost:8000/api/screening/results/latest | python3 -m json.tool

# 사용 가능한 전략 목록
screen-strategies:
    @curl -s http://localhost:8000/api/screening/strategies | python3 -m json.tool

# ──────────────────────────────────────
# 장 마감 분석 (섹터 + 뉴스)
# ──────────────────────────────────────

# 업종-종목 매핑 동기화 (최초 1회 or 월 1회)
sector-sync:
    cd backend && uv run python -m scripts.test_sector_analysis --step sync

# 전종목 데이터 수집
sector-collect:
    cd backend && uv run python -m scripts.test_sector_analysis --step collect

# 섹터 분석만 실행
sector-analyze:
    cd backend && uv run python -m scripts.test_sector_analysis --step analyze

# 뉴스 크롤링 테스트
sector-news:
    cd backend && uv run python -m scripts.test_sector_analysis --step news

# 분석 + 뉴스 + AI 요약
sector-analyze-news:
    cd backend && uv run python -m scripts.test_sector_analysis --step analyze-news

# 최신 결과 텔레그램 발송
sector-telegram:
    cd backend && uv run python -m scripts.test_sector_analysis --step telegram

# 통합 리포트 (수집 → 분석 → 텔레그램)
sector-report:
    cd backend && uv run python -m scripts.test_sector_analysis --step report

# 전체 파이프라인
sector-all:
    cd backend && uv run python -m scripts.test_sector_analysis --step all

# API로 섹터 분석 수동 실행
sector-api:
    @curl -s -X POST http://localhost:8000/api/analysis/sectors/run | python3 -m json.tool

# 최신 분석 결과 조회
sector-latest:
    @curl -s http://localhost:8000/api/analysis/sectors/latest | python3 -m json.tool

# 업종 마스터 목록
sector-list:
    @curl -s http://localhost:8000/api/analysis/sectors/list | python3 -m json.tool

# ──────────────────────────────────────
# 텔레그램
# ──────────────────────────────────────

# 텔레그램 테스트 발송
telegram-test:
    @curl -s -X POST http://localhost:8000/api/settings/telegram/test | python3 -m json.tool

# ──────────────────────────────────────
# 설정
# ──────────────────────────────────────

# 현재 설정 조회
settings:
    @curl -s http://localhost:8000/api/settings | python3 -m json.tool

# ──────────────────────────────────────
# 종목 데이터
# ──────────────────────────────────────

# 종목 검색 (예: just stock-search 삼성)
stock-search query:
    @curl -s "http://localhost:8000/api/stocks?q={{ query }}" | python3 -m json.tool

# 종목 OHLCV 조회 (예: just stock-ohlcv 005930 120)
stock-ohlcv ticker days="60":
    @curl -s "http://localhost:8000/api/stocks/{{ ticker }}/ohlcv?days={{ days }}" | python3 -m json.tool

# ──────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────

# DB 파일 크기 확인
db-size:
    @ls -lh backend/data/screening.db 2>/dev/null || echo "DB 파일 없음"

# DB 테이블 목록 & 행 수
db-stats:
    @sqlite3 backend/data/screening.db ".tables" 2>/dev/null || echo "DB 파일 없음"
    @echo "---"
    @sqlite3 backend/data/screening.db \
        "SELECT 'stock: ' || COUNT(*) FROM stock UNION ALL \
         SELECT 'dailyohlcv: ' || COUNT(*) FROM dailyohlcv UNION ALL \
         SELECT 'investortrading: ' || COUNT(*) FROM investortrading UNION ALL \
         SELECT 'screeningresult: ' || COUNT(*) FROM screeningresult UNION ALL \
         SELECT 'screeningsummary: ' || COUNT(*) FROM screeningsummary;" \
        2>/dev/null || echo "테이블 조회 실패"

# ──────────────────────────────────────
# 정적 사이트 (GitHub Pages)
# ──────────────────────────────────────

# 정적 사이트 생성 (오늘 날짜)
site-generate:
    cd backend && uv run python -m scripts.generate_site

# 특정 날짜 사이트 생성 (예: just site-date 2026-02-13)
site-date date:
    cd backend && uv run python -m scripts.generate_site --date {{ date }}

# 사이트 생성 + 텔레그램 발송
site-telegram:
    cd backend && uv run python -m scripts.generate_site --telegram

# 로컬 미리보기 (http://localhost:8080)
site-preview:
    @echo "🌐 http://localhost:8080 에서 미리보기"
    cd site && python3 -m http.server 8080

# ──────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────

# 프로젝트 코드 라인 수
loc:
    @echo "=== Backend (Python) ===" && \
    find backend/src -name "*.py" | xargs wc -l | tail -1 && \
    echo "=== Tests ===" && \
    find backend/tests -name "*.py" | xargs wc -l | tail -1
