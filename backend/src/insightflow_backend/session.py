"""In-memory, single-tenant session state for Phase 5.

Real persistence/multi-tenancy is Phase 6 (CLAUDE.md); this holds exactly one dataset's
SemanticModel + data_dir + lazily-built pipelines for the whole process lifetime. A new
/schema/discover call replaces it and invalidates every cached pipeline so a second dataset
never silently answers queries against the first dataset's stale DuckDB views.
"""
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request

from insightflow_core.models import SemanticModel
from insightflow_core.pipeline import AnalyticsEnginePipeline

from insightflow_backend import wiring


@dataclass
class SessionState:
    semantic_model: Optional[SemanticModel] = None
    data_dir: Optional[str] = None
    _engine: Optional[AnalyticsEnginePipeline] = field(default=None, repr=False)
    _dashboard_pipeline: object = field(default=None, repr=False)
    _chat_pipeline: object = field(default=None, repr=False)

    def new_upload_dir(self, upload_root: Path) -> Path:
        d = upload_root / uuid.uuid4().hex
        d.mkdir(parents=True, exist_ok=True)
        return d

    def clear_pipelines(self) -> None:
        self._engine = None
        self._dashboard_pipeline = None
        self._chat_pipeline = None

    def reset(self) -> None:
        self.semantic_model = None
        self.data_dir = None
        self.clear_pipelines()

    def get_or_build_engine(self) -> AnalyticsEnginePipeline:
        if self._engine is None:
            self._engine = wiring.build_analytics_engine(self.semantic_model, self.data_dir)
        return self._engine

    def get_or_build_dashboard_pipeline(self):
        if self._dashboard_pipeline is None:
            self._dashboard_pipeline = wiring.build_dashboard(self.semantic_model, self.data_dir)
        return self._dashboard_pipeline

    def get_or_build_chat_pipeline(self):
        if self._chat_pipeline is None:
            engine = self.get_or_build_engine()
            self._chat_pipeline = wiring.build_chatbot(self.semantic_model, self.data_dir, engine)
        return self._chat_pipeline


def get_session(request: Request) -> SessionState:
    return request.app.state.session


def require_schema(session: SessionState) -> None:
    if session.semantic_model is None:
        raise HTTPException(status_code=409, detail="no dataset uploaded yet -- call POST /schema/discover first")
