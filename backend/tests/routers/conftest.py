"""Router-level auth tests use a real Postgres-backed FastAPI TestClient -- the app's own
`get_db` dependency is overridden to hand out a session bound to a per-test transaction (rolled
back afterward), same isolation strategy as tests/db/conftest.py, just wired through FastAPI's
dependency_overrides instead of used directly.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from insightflow_backend.db.session import get_db
from insightflow_backend.main import create_app
from tests.db.conftest import db_engine, requires_postgres  # noqa: F401 -- reused fixture

__all__ = ["requires_postgres"]


@pytest.fixture
def client(db_engine):  # noqa: F811 -- fixture name shadows the imported one on purpose
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
