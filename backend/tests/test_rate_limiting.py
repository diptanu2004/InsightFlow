"""Integration check that the rate limiter is actually wired into /auth/register and /auth/login
-- tests/auth/test_rate_limit.py already covers RedisRateLimiter's own logic in isolation.
Uses its own TestClient (not the shared `client` fixture) so it can shrink the limit via
settings before the app -- and its app.state.auth_rate_limiter -- is built.
"""
import pytest

from insightflow_backend.auth.rate_limit import build_redis_client
from insightflow_backend.config import settings
from tests.db.conftest import db_engine  # noqa: F401 -- reused fixture
from tests.infra import clear_rate_limit_keys, real_db_client, requires_postgres, requires_redis


@pytest.fixture
def tight_limit_client(db_engine, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "auth_rate_limit_max_requests", 2)
    monkeypatch.setattr(settings, "auth_rate_limit_window_seconds", 60.0)
    clear_rate_limit_keys(build_redis_client(), "auth")
    with real_db_client(db_engine) as test_client:
        yield test_client


@requires_postgres
@requires_redis
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
@requires_redis
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


@requires_redis
def test_rate_limit_is_shared_across_separate_app_instances(monkeypatch):
    """The whole point of moving off InMemoryRateLimiter: two `create_app()` calls (standing in
    for two backend processes behind a load balancer) must share one global cap, not one each.
    """
    monkeypatch.setattr(settings, "auth_rate_limit_max_requests", 2)
    monkeypatch.setattr(settings, "auth_rate_limit_window_seconds", 60.0)
    clear_rate_limit_keys(build_redis_client(), "auth")

    from insightflow_backend.auth.rate_limit import build_auth_rate_limiter

    client = build_redis_client()
    instance_a = build_auth_rate_limiter(client)
    instance_b = build_auth_rate_limiter(client)

    instance_a.check("shared-client")
    instance_b.check("shared-client")  # different instance, same Redis -- still counts toward 2
    with pytest.raises(Exception):
        instance_a.check("shared-client")  # 3rd request across both instances -- over the cap
