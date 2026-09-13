import time as time_module

import pytest
from fastapi import HTTPException

from insightflow_backend.auth.rate_limit import InMemoryRateLimiter


def test_allows_requests_under_the_limit():
    limiter = InMemoryRateLimiter(max_requests=3, window_seconds=60)
    for _ in range(3):
        limiter.check("client-a")  # should not raise


def test_blocks_requests_over_the_limit():
    limiter = InMemoryRateLimiter(max_requests=2, window_seconds=60)
    limiter.check("client-a")
    limiter.check("client-a")
    with pytest.raises(HTTPException) as exc_info:
        limiter.check("client-a")
    assert exc_info.value.status_code == 429


def test_different_keys_have_independent_buckets():
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=60)
    limiter.check("client-a")
    limiter.check("client-b")  # different key -- should not raise despite client-a being at limit


def test_old_hits_expire_out_of_the_window(monkeypatch):
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=10)
    fake_now = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: fake_now[0])

    limiter.check("client-a")
    fake_now[0] += 11  # past the 10s window
    limiter.check("client-a")  # should not raise -- the earlier hit has expired
