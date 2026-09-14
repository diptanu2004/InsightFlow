import logging
import time

import groq
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

from insightflow_backend.auth.rate_limit import build_auth_rate_limiter, build_llm_rate_limiter, build_redis_client
from insightflow_backend.cache import build_result_cache
from insightflow_backend.config import settings
from insightflow_backend.logging_config import configure_logging
from insightflow_backend.logging_context import get_org_id, get_user_id, new_request_id
from insightflow_backend.pipeline_cache import PipelineCache
from insightflow_backend.routers import analytics, auth, chat, dashboard, health, organizations, projects, schema
from insightflow_backend.storage import build_storage

logger = logging.getLogger("insightflow")


def create_app() -> FastAPI:
    configure_logging()

    # Fail fast rather than silently signing every access token with an empty key -- a JWT
    # "signed" with "" verifies against any other empty-secret deployment and is one env var
    # typo away from being trivially forgeable.
    if not settings.jwt_secret:
        raise RuntimeError(
            "JWT_SECRET is not set. Refusing to start: an empty secret would sign every access "
            "token with no real key. Set JWT_SECRET in the environment (see backend/.env.example)."
        )

    app = FastAPI(
        title="InsightFlow Backend",
        version="0.3.0",
        description="Phase 5/6 -- FastAPI app wiring POC 1-4's pipelines behind HTTP routes, "
        "with auth + multi-tenancy (Postgres, JWT, RBAC, S3-compatible dataset storage).",
    )

    # One ObjectStorage instance for the whole process, shared by the upload route and the
    # pipeline cache (both need it) -- see dataset_deps.py / pipeline_cache.py. Replaces Phase
    # 5's single global SessionState: state is now per-dataset (PipelineCache, keyed by
    # dataset_id) and per-tenant data lives in Postgres, not in this process's memory at all.
    storage = build_storage()
    app.state.storage = storage
    app.state.pipeline_cache = PipelineCache(storage=storage)
    # One Redis client for the whole process, shared by both rate-limit buckets, the result
    # cache, and the job queue in a later Phase 7 milestone. Held on app.state, not a module
    # global -- see auth/rate_limit.py's docstring for why (module globals would let every
    # FastAPI app instance in the same process, including every independent test's TestClient
    # app, throttle each other's unrelated requests).
    redis_client = build_redis_client()
    app.state.redis_client = redis_client
    app.state.auth_rate_limiter = build_auth_rate_limiter(redis_client)
    app.state.llm_rate_limiter = build_llm_rate_limiter(redis_client)
    app.state.result_cache = build_result_cache(redis_client, ttl_seconds=settings.result_cache_ttl_seconds)

    # Fails closed: no frontend exists yet (Phase 8), so CORS_ALLOWED_ORIGINS is empty by
    # default and no browser origin is allowed until an operator opts in explicitly.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        request_id = new_request_id()
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000, 2)
        # user_id/org_id are populated by get_current_user/require_org_role/require_project_role
        # while the route's own dependencies resolve -- read back here, after the fact, since
        # this middleware runs before those dependencies do (see logging_context.py).
        logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "user_id": get_user_id(),
                "org_id": get_org_id(),
            },
        )
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(groq.RateLimitError)
    async def llm_rate_limited(request: Request, exc: groq.RateLimitError) -> JSONResponse:
        # The AI provider's own quota (not this backend's rate limiter) -- previously an unhandled
        # 500 from dashboard/generate and chat/ask, found when Groq's daily token limit ran out. It's a
        # temporary, external condition, so 503 plus the provider's retry hint, not a server fault.
        retry_after = exc.response.headers.get("retry-after") if exc.response is not None else None
        headers = {"Retry-After": retry_after} if retry_after else {}
        return JSONResponse(
            status_code=503,
            content={"detail": "The AI provider's usage limit has been reached. Try again in a few minutes."},
            headers=headers,
        )

    @app.exception_handler(groq.APIConnectionError)
    @app.exception_handler(groq.InternalServerError)
    async def llm_unavailable(request: Request, exc: groq.APIError) -> JSONResponse:
        # Timeouts/connection failures (APITimeoutError is an APIConnectionError) and the provider's own
        # 5xx -- transient, external, and previously unhandled 500s: every chat question in a live test
        # failed that way during a stretch of Groq timeouts.
        return JSONResponse(
            status_code=503,
            content={"detail": "The AI provider didn't respond. Try again in a moment."},
        )

    @app.exception_handler(groq.APIStatusError)
    async def llm_rejected(request: Request, exc: groq.APIStatusError) -> JSONResponse:
        # Any other provider error response (RateLimitError and InternalServerError are subclasses with
        # their own, more specific handlers above). Found as a raw 500 when an explanation prompt grew
        # past the provider's request-size limit (413). Logged so a request-shape bug here isn't hidden
        # behind a friendly message.
        logger.warning("llm_provider_rejected_request", extra={"status_code": exc.status_code})
        return JSONResponse(
            status_code=503,
            content={"detail": "The AI provider couldn't process this request. Try rephrasing or narrowing the question."},
        )

    app.include_router(health.router)
    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(organizations.router, prefix="/organizations", tags=["organizations"])
    # The remaining routers are mixed-prefix (routes shaped /projects/{project_id}/...) --
    # mounted with no app-level prefix, since each route's own path already includes it.
    app.include_router(projects.router)
    app.include_router(schema.router)
    app.include_router(analytics.router)
    app.include_router(dashboard.router)
    app.include_router(chat.router)
    return app


app = create_app()
