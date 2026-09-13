"""Dependency-injection glue: builds each POC's pipeline from the current session's
SemanticModel + data_dir, reusing every POC's own convenience builder unchanged.

Nothing here re-implements pipeline logic -- it only calls SchemaDiscoveryPipeline.run(),
insightflow_core's build_pipeline(), insightflow_dashboard's build_dashboard_pipeline(), and
insightflow_chatbot's build_question_answering_pipeline() with real arguments, exactly like each
POC's own scripts/run_poc*.py already does.
"""
import json

from insightflow_core.models import SemanticModel as CoreSemanticModel
from insightflow_core.pipeline import AnalyticsEnginePipeline, build_pipeline
from insightflow_chatbot.llm.groq_client import GroqLLMClient as ChatbotGroqLLMClient
from insightflow_chatbot.pipeline import QuestionAnsweringPipeline, build_question_answering_pipeline
from insightflow_dashboard.dashboard.pipeline import DashboardGenerationPipeline, build_dashboard_pipeline
from insightflow_dashboard.llm.groq_client import GroqLLMClient as DashboardGroqLLMClient
from insightflow_schema_discovery.llm.groq_client import GroqLLMClient as SchemaGroqLLMClient
from insightflow_schema_discovery.pipeline import SchemaDiscoveryPipeline

from insightflow_backend.config import settings
from insightflow_backend.registry.bootstrap import bootstrap_registry


def run_schema_discovery(filepaths: list[str]) -> CoreSemanticModel:
    """Run POC 1's pipeline, then bridge POC 1's own SemanticModel into insightflow_core's --
    the same JSON round-trip every existing script already uses to cross this boundary (the two
    classes are field-identical but not the same Pydantic model; see insightflow_core's
    semantic_model.py docstring). Not a new pattern, just done in-memory instead of via a file.
    """
    poc1_pipeline = SchemaDiscoveryPipeline(llm_client=SchemaGroqLLMClient())
    poc1_model = poc1_pipeline.run(filepaths)
    return CoreSemanticModel(**json.loads(poc1_model.model_dump_json()))


def build_analytics_engine(semantic_model: CoreSemanticModel, data_dir: str) -> AnalyticsEnginePipeline:
    registry = bootstrap_registry()
    return build_pipeline(
        semantic_model,
        registry,
        data_dir,
        duckdb_path=settings.duckdb_path,
        query_timeout_seconds=settings.query_timeout_seconds,
        max_row_limit=settings.max_row_limit,
    )


def build_dashboard(semantic_model: CoreSemanticModel, data_dir: str) -> DashboardGenerationPipeline:
    registry = bootstrap_registry()
    return build_dashboard_pipeline(
        semantic_model,
        registry,
        data_dir,
        DashboardGroqLLMClient(),
        min_components=settings.dashboard_min_components,
        max_components=settings.dashboard_max_components,
    )


def build_chatbot(
    semantic_model: CoreSemanticModel, data_dir: str, engine: AnalyticsEnginePipeline
) -> QuestionAnsweringPipeline:
    # Reuses the SAME already-built engine the analytics router uses -- build_question_answering_
    # pipeline requires one as a required arg (unlike build_dashboard_pipeline, which builds its
    # own internally), so calling build_pipeline() a second time here would double-register
    # DuckDB sources for no reason.
    registry = bootstrap_registry()
    return build_question_answering_pipeline(
        semantic_model,
        registry,
        engine,
        ChatbotGroqLLMClient(),
        data_dir,
        time_entity=settings.time_entity,
        time_field=settings.time_field,
        max_row_limit=settings.max_row_limit,
    )
