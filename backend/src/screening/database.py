"""SQLite + SQLModel 데이터베이스 모듈"""

import logging
from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine, text

from screening.config import settings

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},
)


def get_session() -> Generator[Session, None, None]:
    """DB 세션 제너레이터 (FastAPI Depends 용)"""
    with Session(engine) as session:
        yield session


def create_db_and_tables() -> None:
    """모든 테이블 생성"""
    # 모든 모델을 import하여 SQLModel.metadata에 등록
    import screening.analysis.models  # noqa: F401
    import screening.models.financial  # noqa: F401
    import screening.models.screening_result  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _migrate_add_columns()


def _migrate_add_columns() -> None:
    """기존 테이블에 누락된 컬럼 추가 (SQLite ALTER TABLE)"""
    migrations = [
        (
            "screening_summary",
            "financial_passed",
            "ALTER TABLE screening_summary ADD COLUMN financial_passed INTEGER DEFAULT 0",
        ),
        (
            "screening_results",
            "change_pct",
            "ALTER TABLE screening_results ADD COLUMN change_pct REAL DEFAULT 0",
        ),
        (
            "screening_results",
            "sector",
            "ALTER TABLE screening_results ADD COLUMN sector VARCHAR(50) DEFAULT ''",
        ),
    ]
    with Session(engine) as session:
        for table, column, sql in migrations:
            try:
                result = session.execute(text(f"PRAGMA table_info({table})"))
                cols = {r[1] for r in result.all()}
                if column not in cols:
                    session.execute(text(sql))
                    session.commit()
                    logger.info("마이그레이션: %s.%s 컬럼 추가", table, column)
            except Exception:
                logger.debug("마이그레이션 스킵: %s.%s", table, column)
