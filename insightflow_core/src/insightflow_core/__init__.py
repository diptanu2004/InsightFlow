"""insightflow-core: the shared analytics engine (models, compilation, execution, safety,
validation, pipeline orchestration, and the generic MetricRegistry storage class) extracted out
of poc2_analytics_engine so poc3_dashboard_generation (and future POCs) depend on ONE copy of
this code instead of hand-vendoring it.

What stays OUT of this package, deliberately: each POC's own bootstrap_registry() (which
MetricDefinitions/Measures actually get registered is POC-specific business logic, not shared
engine code), POC1's SemanticModel-producing pipeline, and anything LLM-related (POC 2 has no LLM
dependency by design; POC 3's DashboardPlanner is its own thing).

See top-level README.md's "Shared engine package" section for the extraction rationale and how a
future POC should depend on this instead of vendoring a fourth copy.
"""
from insightflow_core.pipeline import AnalyticsEnginePipeline, build_pipeline
from insightflow_core.registry import MetricRegistry

__all__ = ["AnalyticsEnginePipeline", "build_pipeline", "MetricRegistry"]
