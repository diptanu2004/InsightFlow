import logging
import time

from fastapi import FastAPI, Request
from starlette.middleware.cors import CORSMiddleware

from insightflow_backend.auth.rate_limit import build_auth_rate_limiter
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
    # Per-app instance, not a module global -- see auth/rate_limit.py's docstring for why (module
    # globals would let every FastAPI app instance in the same process, including every
    # independent test's TestClient app, throttle each other's unrelated requests).
    app.state.auth_rate_limiter = build_auth_rate_limiter()

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
