"""Shared "is the real infra up?" checks, used to skip (not mock) tests that need
Postgres/MinIO/Redis -- same "verify against the real thing" discipline as every POC's own
integration tests (CLAUDE.md §8). Centralized here instead of duplicated per test file since M5
needs both Postgres and MinIO together (schema discovery now writes to both).
"""
from contextlib import contextmanager

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from insightflow_backend.auth.rate_limit import build_redis_client
from insightflow_backend.config import settings
from insightflow_backend.db.session import get_db
from insightflow_backend.jobs import build_rq_connection
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


def _redis_available() -> bool:
    try:
        build_redis_client().ping()
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(), reason="requires `docker compose up -d postgres` running locally"
)
requires_minio = pytest.mark.skipif(
    not _minio_available(), reason="requires `docker compose up -d minio minio-init` running locally"
)
# Computed once at import (like Postgres/MinIO above), not per-test -- re-pinging per test made
# a full local run take 2+ minutes just to skip cleanly when Redis was down.
_REDIS_AVAILABLE = _redis_available()

requires_redis = pytest.mark.skipif(
    not _REDIS_AVAILABLE, reason="requires `docker compose up -d redis` running locally"
)
requires_infra = pytest.mark.skipif(
    not (_postgres_available() and _minio_available()),
    reason="requires `docker compose up -d postgres minio minio-init` running locally",
)


def skip_if_redis_unavailable() -> None:
    """Every route behind /auth/register or /auth/login now depends on Redis (rate_limit_auth) --
    call this from any fixture that builds a real app and authenticates through it, so those
    tests skip cleanly instead of failing with a raw connection error when Redis isn't running.
    """
    if not _REDIS_AVAILABLE:
        pytest.skip("requires `docker compose up -d redis` running locally")


def reset_rate_limits() -> None:
    """Real Redis state persists across the whole test session (unlike the old in-memory
    limiter, which got a fresh dict every `create_app()` call), and every TestClient shares the
    same fake client IP ("testclient") -- so every test that merely authenticates as setup, not
    as its own rate-limit test, needs a clean bucket first or it inherits however much of the
    global cap earlier tests in the same run already spent. Call from any fixture that
    authenticates through a real app, the same way DB fixtures roll back their own transaction.
    """
    if _REDIS_AVAILABLE:
        client = build_redis_client()
        clear_rate_limit_keys(client, "auth")
        clear_rate_limit_keys(client, "llm")


def run_pending_jobs() -> None:
    """Drains whatever's on the discovery job queue right now, synchronously, in this process --
    a real RQ `Worker` in burst mode (processes everything queued, then returns) rather than a
    long-running background process. This still exercises the real job body
    (`jobs.run_discovery_job`) through RQ's real dequeue/execute path, not a mock standing in
    for the worker -- just without needing a separate `docker compose up worker` process for
    every test. A real separate worker process is verified once, end to end, in M4.
    """
    from rq.worker import SimpleWorker

    SimpleWorker([settings.rq_queue_name], connection=build_rq_connection()).work(burst=True)


def clear_rate_limit_keys(client, prefix: str) -> None:
    """Real Redis state persists across test runs (unlike the old in-memory limiter, which got a
    fresh dict every `create_app()` call) -- tests that assert on exact counts must start from a
    clean bucket rather than whatever a previous run left behind.
    """
    for key in client.scan_iter(match=f"ratelimit:{prefix}:*"):
        client.delete(key)


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


@contextmanager
def committing_db_client():
    """Like `real_db_client`, but writes commit for real instead of being rolled back at the end
    -- required for any test that exercises the async schema-discovery job. `jobs.py`'s worker
    opens its own DB session via the app's normal production `SessionLocal`/`get_db` (a
    different connection than whatever `real_db_client` wraps in an unfinished transaction), so
    it can only see rows another connection has actually committed -- a Job row created inside
    real_db_client's still-open, never-committed transaction is invisible to it, and the worker
    silently no-ops (`jobs.run_discovery_job` finds no such Job and returns early), which is
    exactly the bug this fixture exists to avoid.

    No `get_db` override at all: the app just uses its real production dependency, so routes and
    the worker share one "real commits, real Postgres" world. There's no per-test rollback here,
    so callers must keep test data (emails, org/project names) unique within a module and rely
    on the module-scoped `db_engine` fixture's own `drop_all()` for cleanup between modules.
    """
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
