"""DB tests run against a real Postgres (docker-compose's `postgres` service), not sqlite --
same "verify against the real thing" discipline as every POC's own integration tests
(CLAUDE.md §8): Postgres-specific types used here (UUID, JSONB, native ENUM) don't behave
identically on sqlite, so a passing sqlite test would prove less than nothing.

Requires `docker compose up -d postgres` to be running locally; skipped otherwise so the rest of
the suite (which doesn't need Postgres) still runs without Docker. See tests/infra.py for the
availability check, shared with the other real-infra test modules.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from insightflow_backend.config import settings
from insightflow_backend.db.base import Base
from insightflow_backend.db import models  # noqa: F401 -- registers tables on Base.metadata
from tests.infra import requires_postgres  # noqa: F401 -- re-exported for existing importers

__all__ = ["requires_postgres", "db_engine", "db_session"]


@pytest.fixture(scope="module")
def db_engine():
    engine = create_engine(settings.database_url)
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
