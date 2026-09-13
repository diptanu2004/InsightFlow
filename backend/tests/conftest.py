"""Root fixtures used by tests living directly under tests/ (e.g. the end-to-end smoke test).
Phase 6 replaced the single global in-memory SessionState with real Postgres + S3/MinIO state, so
even a "just hit the API" smoke test now needs both running -- see tests/infra.py for the
DB-transaction-per-test isolation strategy this `client` fixture shares with tests/routers/.
"""
from pathlib import Path

import pytest

from tests.db.conftest import db_engine  # noqa: F401 -- reused fixture
from tests.infra import committing_db_client, real_db_client, reset_rate_limits, skip_if_redis_unavailable

SAMPLE_DATA_DIR = Path(__file__).parent.parent / "data" / "raw" / "sample"


@pytest.fixture
def client(db_engine):  # noqa: F811 -- fixture name shadows the imported one on purpose
    skip_if_redis_unavailable()  # /auth/register (used to obtain a token) is now Redis-gated
    reset_rate_limits()  # every TestClient shares one fake IP -- start each test with a clean bucket
    with real_db_client(db_engine) as test_client:
        yield test_client


@pytest.fixture
def committing_client(db_engine):  # noqa: F811 -- ensures tables exist via db_engine's create_all/drop_all
    """For tests exercising the async schema-discovery job -- see committing_db_client's
    docstring for why the ordinary rollback-per-test `client` fixture can't be used there.
    """
    skip_if_redis_unavailable()
    reset_rate_limits()
    with committing_db_client() as test_client:
        yield test_client
