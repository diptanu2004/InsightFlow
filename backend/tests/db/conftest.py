"""DB tests run against a real Postgres (docker-compose's `postgres` service), not sqlite --
same "verify against the real thing" discipline as every POC's own integration tests
(CLAUDE.md §8): Postgres-specific types used here (UUID, JSONB, native ENUM) don't behave
identically on sqlite, so a passing sqlite test would prove less than nothing.

Requires `docker compose up -d postgres` to be running locally; skipped otherwise so the rest of
the suite (which doesn't need Postgres) still runs without Docker.
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from insightflow_backend.db.base import Base
from insightflow_backend.db import models  # noqa: F401 -- registers tables on Base.metadata

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg://insightflow:insightflow@localhost:5432/insightflow"
)


def _postgres_available() -> bool:
    try:
        create_engine(TEST_DATABASE_URL).connect().close()
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(), reason="requires `docker compose up -d postgres` running locally"
)


@pytest.fixture(scope="module")
def db_engine():
    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(db_engine) -> Session:
    """One transaction per test, rolled back at the end -- tests never see each other's rows
    even though they share the same tables/engine for the whole module.
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        # A test that intentionally triggers an IntegrityError (constraint tests) already
        # deassociates this connection-level transaction via the session's own rollback --
        # rolling back twice is a no-op there, just needs to tolerate "already deassociated".
        if transaction.is_active:
            transaction.rollback()
        connection.close()
