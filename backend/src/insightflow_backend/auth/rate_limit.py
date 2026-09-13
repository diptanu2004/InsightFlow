"""In-memory, per-process, IP-keyed rate limiting for /auth/login and /auth/register only --
a deliberate stopgap, not the real answer. It resets on every restart, doesn't share state
across multiple backend instances, and a client behind a shared NAT/proxy shares one bucket with
everyone else behind it. Real rate limiting (Redis-backed, shared across instances, covering more
than just auth) is explicitly Phase 7 (CLAUDE.md's development order) -- this exists only so a
bare Phase 6 deployment isn't wide open to unlimited credential-stuffing/signup-spam attempts
before Phase 7 lands.
"""
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from insightflow_backend.config import settings


class InMemoryRateLimiter:
    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self._window_seconds:
            hits.popleft()
        if len(hits) >= self._max_requests:
            raise HTTPException(status_code=429, detail="too many requests -- try again later")
        hits.append(now)


def build_auth_rate_limiter() -> InMemoryRateLimiter:
    return InMemoryRateLimiter(
        max_requests=settings.auth_rate_limit_max_requests,
        window_seconds=settings.auth_rate_limit_window_seconds,
    )


def rate_limit_auth(request: Request) -> None:
    # Lives on app.state (set in main.py's create_app()), not a module-level singleton -- a
    # module global would leak state across every FastAPI app instance in the same process,
    # including every independent TestClient app in the test suite, which all share the same
    # `testclient` fake client IP and would otherwise throttle each other's unrelated tests.
    client_ip = request.client.host if request.client else "unknown"
    request.app.state.auth_rate_limiter.check(client_ip)
