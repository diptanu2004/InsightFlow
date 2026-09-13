from fastapi import FastAPI

from insightflow_backend.config import settings
from insightflow_backend.routers import analytics, auth, chat, dashboard, health, organizations, projects, schema
from insightflow_backend.session import SessionState


def create_app() -> FastAPI:
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
        version="0.2.0",
        description="Phase 5/6 -- FastAPI app wiring POC 1-4's pipelines behind HTTP routes, "
        "now with auth + multi-tenancy.",
    )
    app.state.session = SessionState()

    app.include_router(health.router)
    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(organizations.router, prefix="/organizations", tags=["organizations"])
    app.include_router(projects.router)  # mixed-prefix router; see routers/projects.py docstring
    app.include_router(schema.router, prefix="/schema", tags=["schema"])
    app.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
    app.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
    app.include_router(chat.router, prefix="/chat", tags=["chat"])
    return app


app = create_app()
