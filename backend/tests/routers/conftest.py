"""Router-level tests use a real Postgres-backed FastAPI TestClient -- see tests/infra.py's
`real_db_client` for the shared DB-transaction-per-test isolation strategy.
"""
import pytest

from tests.db.conftest import db_engine, requires_postgres  # noqa: F401 -- reused fixture
from tests.infra import real_db_client, requires_infra  # noqa: F401 -- reused by dataset-upload tests

__all__ = ["requires_postgres", "requires_infra"]


@pytest.fixture
def client(db_engine):  # noqa: F811 -- fixture name shadows the imported one on purpose
    with real_db_client(db_engine) as test_client:
        yield test_client
