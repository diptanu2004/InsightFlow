"""Integration check that the rate limiter is actually wired into /auth/register and /auth/login
-- tests/auth/test_rate_limit.py already covers InMemoryRateLimiter's own logic in isolation.
Uses its own TestClient (not the shared `client` fixture) so it can shrink the limit via
settings before the app -- and its app.state.auth_rate_limiter -- is built.
"""
import pytest

from insightflow_backend.config import settings
from tests.db.conftest import db_engine  # noqa: F401 -- reused fixture
from tests.infra import real_db_client, requires_postgres


@pytest.fixture
def tight_limit_client(db_engine, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "auth_rate_limit_max_requests", 2)
    monkeypatch.setattr(settings, "auth_rate_limit_window_seconds", 60.0)
    with real_db_client(db_engine) as test_client:
        yield test_client


@requires_postgres
def test_register_is_rate_limited_after_the_configured_max(tight_limit_client):
    for i in range(2):
        r = tight_limit_client.post(
            "/auth/register", json={"email": f"user{i}@example.com", "password": "correct-horse-battery-staple"}
        )
        assert r.status_code == 201, r.text

    r = tight_limit_client.post(
        "/auth/register", json={"email": "user-over-limit@example.com", "password": "correct-horse-battery-staple"}
    )
    assert r.status_code == 429


@requires_postgres
def test_login_is_rate_limited_after_the_configured_max(tight_limit_client):
    tight_limit_client.post(
        "/auth/register", json={"email": "login-target@example.com", "password": "correct-horse-battery-staple"}
    )
    # That register call already consumed one of the 2 allotted slots -- one more login attempt
    # is allowed, then the next is blocked.
    r = tight_limit_client.post(
        "/auth/login", json={"email": "login-target@example.com", "password": "wrong-password"}
    )
    assert r.status_code == 401  # still under the limit, just wrong credentials

    r = tight_limit_client.post(
        "/auth/login", json={"email": "login-target@example.com", "password": "wrong-password"}
    )
    assert r.status_code == 429
