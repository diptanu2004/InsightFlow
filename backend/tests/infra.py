"""Shared "is the real infra up?" checks, used to skip (not mock) tests that need Postgres/MinIO
-- same "verify against the real thing" discipline as every POC's own integration tests
(CLAUDE.md §8). Centralized here instead of duplicated per test file since M5 needs both
Postgres and MinIO together (schema discovery now writes to both).
"""
from contextlib import contextmanager

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from insightflow_backend.config import settings
from insightflow_backend.db.session import get_db
from insightflow_backend.main import create_app
from insightflow_backend.storage import build_storage


def _postgres_available() -> bool:
    try:
        create_engine(settings.database_url).connect().close()
        return True
    except Exception:
        return False


def _minio_available() -> bool:
    try:
        build_storage().exists("__connectivity_probe__")
        return True
    except (EndpointConnectionError, ClientError):
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(), reason="requires `docker compose up -d postgres` running locally"
)
requires_minio = pytest.mark.skipif(
    not _minio_available(), reason="requires `docker compose up -d minio minio-init` running locally"
)
requires_infra = pytest.mark.skipif(
    not (_postgres_available() and _minio_available()),
    reason="requires `docker compose up -d postgres minio minio-init` running locally",
)


@contextmanager
def real_db_client(db_engine):
    """A FastAPI TestClient wired to a real DB transaction that's rolled back afterward -- the
    shared "client" fixture body for every conftest that needs one (root, tests/routers/), so
    the isolation strategy (one transaction per test, rolled back, never committed) lives in
    exactly one place.
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection)

    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db

    with TestClient(app) as test_client:
        yield test_client

    if transaction.is_active:
        transaction.rollback()
    connection.close()
