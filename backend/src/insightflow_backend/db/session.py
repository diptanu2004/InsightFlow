"""Sync SQLAlchemy engine/session -- deliberately not async. Every route handler in this backend
is a sync `def` (the POC pipelines underneath are sync DuckDB/pandas code), so FastAPI already
runs them in a threadpool; an async DB driver would buy nothing here and would mean maintaining
two DB code paths (this one and each POC pipeline's sync one) for no benefit.
"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from insightflow_backend.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
