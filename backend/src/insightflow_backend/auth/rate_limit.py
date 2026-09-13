"""Redis-backed rate limiting (Phase 7 M1) -- a real global cap shared across every backend
instance, replacing the old `InMemoryRateLimiter` stopgap that reset on restart and let each
process under a load balancer enforce its own separate limit. See backend/docs/phase7_scope.md.

Two independently-configured buckets share the same `RedisRateLimiter` class:
- "auth": /auth/login and /auth/register, keyed by client IP (a per-client abuse concern).
- "llm": schema/discover, dashboard/generate, chat/ask, keyed by project_id (an LLM-spend
  budget concern -- every user in an org/project shares the same limit, not one per client).
"""
import redis
from fastapi import HTTPException, Request

from insightflow_backend.config import settings


class RedisRateLimiter:
    """Fixed-window counter per key. INCR+EXPIRE isn't atomic, so a key expiring in the gap
    between the two calls can very rarely let a window run slightly over -- the same tolerance
    the in-memory version had. This is a stopgap against abuse, not a precise quota system.
    """

    def __init__(self, client: redis.Redis, prefix: str, max_requests: int, window_seconds: float) -> None:
        self._client = client
        self._prefix = prefix
        self._max_requests = max_requests
        self._window_seconds = window_seconds

    def check(self, key: str) -> None:
        redis_key = f"ratelimit:{self._prefix}:{key}"
        count = self._client.incr(redis_key)
        if count == 1:
            self._client.expire(redis_key, max(int(self._window_seconds), 1))
        if count > self._max_requests:
            raise HTTPException(status_code=429, detail="too many requests -- try again later")


def build_redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def build_auth_rate_limiter(client: redis.Redis) -> RedisRateLimiter:
    return RedisRateLimiter(
        client,
        prefix="auth",
        max_requests=settings.auth_rate_limit_max_requests,
        window_seconds=settings.auth_rate_limit_window_seconds,
    )


def build_llm_rate_limiter(client: redis.Redis) -> RedisRateLimiter:
    return RedisRateLimiter(
        client,
        prefix="llm",
        max_requests=settings.llm_rate_limit_max_requests,
        window_seconds=settings.llm_rate_limit_window_seconds,
    )


def rate_limit_auth(request: Request) -> None:
    # Lives on app.state (set in main.py's create_app()), not a module-level singleton -- a
    # module global would leak state across every FastAPI app instance in the same process,
    # including every independent TestClient app in the test suite. Redis itself is the real
    # shared state now; app.state just holds this process's client/limiter handle onto it.
    client_ip = request.client.host if request.client else "unknown"
    request.app.state.auth_rate_limiter.check(client_ip)


def rate_limit_llm(request: Request) -> None:
    # project_id is a path param on every route this guards (schema/discover, dashboard/generate,
    # chat/ask are all /projects/{project_id}/...) -- available on `request.path_params` once
    # FastAPI has matched the route, before this dependency runs.
    project_id = request.path_params.get("project_id", "unknown")
    request.app.state.llm_rate_limiter.check(str(project_id))
