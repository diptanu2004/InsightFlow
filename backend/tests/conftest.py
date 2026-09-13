"""Root fixtures used by tests living directly under tests/ (e.g. the end-to-end smoke test).
Phase 6 replaced the single global in-memory SessionState with real Postgres + S3/MinIO state, so
even a "just hit the API" smoke test now needs both running -- see tests/infra.py for the
DB-transaction-per-test isolation strategy this `client` fixture shares with tests/routers/.
"""
from pathlib import Path

import pytest

from tests.db.conftest import db_engine  # noqa: F401 -- reused fixture
from tests.infra import real_db_client

SAMPLE_DATA_DIR = Path(__file__).parent.parent / "data" / "raw" / "sample"


@pytest.fixture
def client(db_engine):  # noqa: F811 -- fixture name shadows the imported one on purpose
    with real_db_client(db_engine) as test_client:
        yield test_client
