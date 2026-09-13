from fastapi import FastAPI

from insightflow_backend.routers import analytics, chat, dashboard, health, schema
from insightflow_backend.session import SessionState


def create_app() -> FastAPI:
    app = FastAPI(
        title="InsightFlow Backend",
        version="0.1.0",
        description="Phase 5 -- one FastAPI app wiring POC 1-4's pipelines behind HTTP routes.",
    )
    app.state.session = SessionState()

    app.include_router(health.router)
    app.include_router(schema.router, prefix="/schema", tags=["schema"])
    app.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
    app.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
    app.include_router(chat.router, prefix="/chat", tags=["chat"])
    return app


app = create_app()
