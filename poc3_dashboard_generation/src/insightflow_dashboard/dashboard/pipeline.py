"""DashboardGenerationPipeline -- orchestrates POC 3 end to end: gather signals -> plan -> validate
-> resolve. Mirrors POC 1's SchemaDiscoveryPipeline and POC 2's AnalyticsEnginePipeline exactly:
the single entry point a future FastAPI route calls directly.

    pipeline = build_dashboard_pipeline(semantic_model, registry, data_dir, llm_client)
    dashboard = pipeline.run()
    return dashboard   # HydratedDashboard is already a pydantic BaseModel

All dependencies are injectable, same as POC 1/POC 2's pipelines, so tests can swap any stage
(e.g. a FakeLLMClient instead of real ChatGroq -- see tests/conftest.py).
"""
from typing import Optional

from insightflow_core.compilation import FieldResolver, SQLCompiler
from insightflow_dashboard.dashboard.hydrated import HydratedDashboard
from insightflow_dashboard.dashboard.planner import DashboardPlanner
from insightflow_dashboard.dashboard.planner_context import MetricSummary, PlannerContext
from insightflow_dashboard.dashboard.resolver import DashboardDataResolver
from insightflow_dashboard.dashboard.signal import SignalGatherer
from insightflow_dashboard.dashboard.validator import SUPPORTED_COMPONENT_TYPES, DashboardValidator
from insightflow_core.execution import QueryExecutor
from insightflow_dashboard.llm.client import LLMClient
from insightflow_core.models import MetricDefinition
from insightflow_core.models.semantic_model import SemanticModel
from insightflow_core.pipeline import AnalyticsEnginePipeline
from insightflow_dashboard.registry import MetricRegistry
from insightflow_core.safety import SQLSafetyChecker
from insightflow_core.validation import ASTValidator
from insightflow_core.compilation.measure_binding import bind_measure
from insightflow_core.validation.metric_resolvability import (
    group_by_problem,
    resolvable_metric_names,
    unresolvable_reason,
)


class DashboardGenerationPipeline:
    def __init__(
        self,
        semantic_model: SemanticModel,
        registry: MetricRegistry,
        signal_gatherer: SignalGatherer,
        planner: DashboardPlanner,
        validator: DashboardValidator,
        resolver: DashboardDataResolver,
        field_resolver: FieldResolver,
    ):
        self.semantic_model = semantic_model
        self.registry = registry
        self.signal_gatherer = signal_gatherer
        self.planner = planner
        self.validator = validator
        self.resolver = resolver
        self.field_resolver = field_resolver

    def run(self) -> HydratedDashboard:
        signals = self.signal_gatherer.gather()
        context = self._build_planner_context(signals)

        # Fail before the planner's LLM call, not after it: with nothing the dataset can compute,
        # every possible spec is invalid, so asking the planner would only spend tokens on a
        # guaranteed rejection. Happens for real whenever the registry's measures name entities
        # the upload doesn't have -- see insightflow_core's metric_resolvability.py.
        if not context.resolvable_metrics:
            reasons = sorted(
                {
                    reason
                    for name in sorted(set(self.registry.measures) | set(self.registry.metrics))
                    if (reason := unresolvable_reason(name, self.registry, self.semantic_model)) is not None
                }
            )
            raise ValueError(f"no registered metric can be computed from this dataset: {'; '.join(reasons)}")

        spec = self.planner.plan(context)

        validation = self.validator.validate(spec)
        if not validation.is_valid:
            raise ValueError(f"invalid dashboard spec: {[e.message for e in validation.errors]}")

        return self.resolver.resolve(spec)

    def _build_planner_context(self, signals) -> PlannerContext:
        entities = [e.name for e in self.semantic_model.entities]
        # Found via the real Groq/real-Olist integration run (examples/real_olist_integration):
        # this used to list every canonical field name, ambiguous ones included. The planner has
        # no way to know a dimension is ambiguous, so it happily proposed GROUP_BY components on
        # "category"/"region" -- both of which DashboardValidator (correctly) rejects, since real
        # POC 1 output maps each to two different entities. Filtering here with the SAME check
        # (FieldResolver.find_entity_for_field) DashboardValidator itself uses means the planner is
        # never told it can use a dimension the validator would reject anyway -- mirrors why
        # SUPPORTED_COMPONENT_TYPES already excludes LINE_CHART for the same reason.
        all_dimension_names = sorted({f.name for e in self.semantic_model.entities for f in e.fields})
        dimensions = []
        for name in all_dimension_names:
            try:
                self.field_resolver.find_entity_for_field(name)
            except (KeyError, ValueError):
                continue
            dimensions.append(name)
        # Same "only offer what the validator would accept" principle as the dimension filter
        # above, applied to metrics: a registered metric that can't resolve against THIS dataset's
        # semantic model is never shown to the planner. DashboardValidator re-checks it anyway.
        runnable = resolvable_metric_names(self.registry, self.semantic_model)
        metrics = [
            # The bound entity, not `measure.entity` -- that's an optional pin and usually unset.
            # Every runnable bare measure binds on its own, so bind_measure can't raise here.
            MetricSummary(
                name=name,
                kind="measure",
                description=f"raw measure on {bind_measure(measure, self.semantic_model).entity}",
            )
            for name, measure in self.registry.measures.items()
            if name in runnable
        ] + [
            MetricSummary(name=name, kind=metric.kind.value, description=metric.description)
            for name, metric in self.registry.metrics.items()
            if isinstance(metric, MetricDefinition) and name in runnable
        ]
        # Found via the real Groq/real-Olist run (examples/real_olist_integration/README.md's
        # second "real bug found" entry): DashboardValidator._validate_group_by_supported_for_metric
        # rejects any bar_chart/pie_chart/table whose metric isn't kind "measure" or "base" (a real
        # POC 2 closure bug -- RATIO/GROWTH/HAVING_RATIO grouping was never implemented). The
        # planner was never told this, so it picked "aov" (kind=ratio) for a bar_chart and burned a
        # real LLM call on a spec the validator was always going to reject. Precomputed here so the
        # prompt can state the constraint explicitly instead of expecting the LLM to infer it from
        # each metric's kind string.
        # Each candidate paired only with dimensions the engine can actually join it to, using the
        # SAME group_by_problem check DashboardValidator re-applies to the returned spec. A metric
        # with no joinable dimension at all isn't groupable on this dataset, whatever its kind.
        groupable_dimensions = {
            m.name: joinable
            for m in metrics
            if m.kind in ("measure", "base")
            and (
                joinable := [
                    d for d in dimensions if group_by_problem(m.name, d, self.registry, self.semantic_model) is None
                ]
            )
        }
        groupable_metrics = list(groupable_dimensions)
        # Found via a third real Groq/real-Olist run (see planner_context.py's field comment):
        # DashboardDataResolver can't resolve a GROWTH-kind metric as ANY component, not just a
        # grouping one -- growth metrics need an explicit comparison-period pair nothing in
        # ComponentSpec/DashboardDataResolver supplies. Excluded here, not just from
        # groupable_metrics, and enforced independently by DashboardValidator
        # (_validate_metric_directly_resolvable) the same double-checking way GROUP_BY-kind
        # restrictions already are.
        resolvable_metrics = [m.name for m in metrics if m.kind != "growth"]
        return PlannerContext(
            entities=entities,
            available_dimensions=dimensions,
            available_metrics=metrics,
            signals=signals,
            supported_component_types=sorted(SUPPORTED_COMPONENT_TYPES, key=lambda t: t.value),
            groupable_metrics=groupable_metrics,
            groupable_dimensions=groupable_dimensions,
            resolvable_metrics=resolvable_metrics,
        )


def build_dashboard_pipeline(
    semantic_model: SemanticModel,
    registry: MetricRegistry,
    data_dir: str,
    llm_client: LLMClient,
    min_components: Optional[int] = None,
    max_components: Optional[int] = None,
) -> DashboardGenerationPipeline:
    """Convenience wiring, mirroring POC 2's build_pipeline(): construct every stage from just a
    SemanticModel + MetricRegistry + data directory + LLM client, using insightflow.config.settings
    for the rest."""
    from insightflow_dashboard.config import settings

    min_c = min_components if min_components is not None else settings.dashboard_min_components
    max_c = max_components if max_components is not None else settings.dashboard_max_components

    field_resolver = FieldResolver(semantic_model)
    executor = QueryExecutor(settings.duckdb_path, settings.query_timeout_seconds, settings.max_row_limit)
    executor.register_sources(semantic_model, data_dir)
    engine = AnalyticsEnginePipeline(
        registry=registry,
        validator=ASTValidator(registry, semantic_model, settings.max_row_limit),
        compiler=SQLCompiler(registry, field_resolver, settings.max_row_limit),
        checker=SQLSafetyChecker(semantic_model, settings.max_row_limit),
        executor=executor,
    )

    return DashboardGenerationPipeline(
        semantic_model=semantic_model,
        registry=registry,
        signal_gatherer=SignalGatherer(engine, semantic_model, registry),
        planner=DashboardPlanner(llm_client, min_c, max_c),
        validator=DashboardValidator(registry, field_resolver, min_c, max_c),
        resolver=DashboardDataResolver(engine),
        field_resolver=field_resolver,
    )
