import pytest
from fastapi import HTTPException

from insightflow_backend.auth.rate_limit import RedisRateLimiter, build_redis_client
from tests.infra import clear_rate_limit_keys, requires_redis

pytestmark = requires_redis


@pytest.fixture
def redis_client():
    client = build_redis_client()
    clear_rate_limit_keys(client, "test")
    yield client
    clear_rate_limit_keys(client, "test")


def test_allows_requests_under_the_limit(redis_client):
    limiter = RedisRateLimiter(redis_client, prefix="test", max_requests=3, window_seconds=60)
    for _ in range(3):
        limiter.check("client-a")  # should not raise


def test_blocks_requests_over_the_limit(redis_client):
    limiter = RedisRateLimiter(redis_client, prefix="test", max_requests=2, window_seconds=60)
    limiter.check("client-a")
    limiter.check("client-a")
    with pytest.raises(HTTPException) as exc_info:
        limiter.check("client-a")
    assert exc_info.value.status_code == 429


def test_different_keys_have_independent_buckets(redis_client):
    limiter = RedisRateLimiter(redis_client, prefix="test", max_requests=1, window_seconds=60)
    limiter.check("client-a")
    limiter.check("client-b")  # different key -- should not raise despite client-a being at limit


def test_old_hits_expire_out_of_the_window(redis_client):
    limiter = RedisRateLimiter(redis_client, prefix="test", max_requests=1, window_seconds=1)
    limiter.check("client-a")
    redis_client.expire("ratelimit:test:client-a", -1)  # force the window to have already elapsed
    limiter.check("client-a")  # should not raise -- the earlier window has expired


def test_different_prefixes_have_independent_buckets(redis_client):
    """The "auth" and "llm" buckets must never share state -- a project hammering chat/ask
    shouldn't be able to lock out its own users' logins, and vice versa.
    """
    auth_limiter = RedisRateLimiter(redis_client, prefix="auth-test", max_requests=1, window_seconds=60)
    llm_limiter = RedisRateLimiter(redis_client, prefix="llm-test", max_requests=1, window_seconds=60)
    try:
        auth_limiter.check("same-key")
        llm_limiter.check("same-key")  # different prefix -- should not raise
    finally:
        clear_rate_limit_keys(redis_client, "auth-test")
        clear_rate_limit_keys(redis_client, "llm-test")
