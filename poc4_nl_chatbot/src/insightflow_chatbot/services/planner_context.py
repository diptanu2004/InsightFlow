"""What QuestionPlanner is allowed to see -- semantic model summary + live registry contents,
never raw source columns or unregistered fields (same information-hiding discipline as POC 1's
profiler-metadata-not-raw-data approach, POC 2's AST never carrying raw column names, and POC 3's
PlannerContext of the same name). Building this is QuestionAnsweringPipeline's job, not the
planner's own -- keeps QuestionPlanner a pure function of (question, context) -> QuestionIntent.
"""
from pydantic import BaseModel, Field

from insightflow_core.compilation import FieldResolver
from insightflow_core.models import MetricDefinition, MetricKind, SemanticModel
from insightflow_core.models.semantic_model import planner_dimension_problem
from insightflow_core.registry import MetricRegistry
from insightflow_core.compilation.measure_binding import bind_measure
from insightflow_core.validation.metric_resolvability import resolvable_metric_names

from insightflow_chatbot.models.time_expression import TimeExpression


class MetricSummary(BaseModel):
    name: str
    kind: str
    description: str


class PlannerContext(BaseModel):
    entities: list[str]
    available_dimensions: list[str]
    available_metrics: list[MetricSummary] = Field(default_factory=list)
    # Only metrics ASTValidator._validate_group_by_supported will accept for GROUP_BY (bare
    # measure or a BASE-kind metric) -- needed for QuestionOperation.GROUP_BY and
    # GROWTH_BY_DIMENSION. Precomputed here rather than expecting the planner to infer the rule
    # from each metric's `kind` string, same lesson POC 3's real-Groq runs learned the hard way
    # (class_diagram.md's "why GROWTH_BY_DIMENSION's two queries must both target a BASE-kind
    # metric" note).
    groupable_metrics: list[str] = Field(default_factory=list)
    # Only metrics of kind GROWTH (e.g. revenue_growth) -- the ONLY metrics valid for a plain
    # QuestionOperation.GROWTH query. Disjoint from groupable_metrics by construction.
    growth_metrics: list[str] = Field(default_factory=list)
    supported_time_expressions: list[TimeExpression] = Field(default_factory=list)


def build_planner_context(semantic_model: SemanticModel, registry: MetricRegistry, field_resolver: FieldResolver) -> PlannerContext:
    entities = [e.name for e in semantic_model.entities]

    # Same ambiguity filter as POC 3's _build_planner_context: only offer a dimension the
    # validator would actually accept, using the SAME check (FieldResolver.find_entity_for_field)
    # QuestionValidator's underlying ASTValidator uses.
    # Categorical fields only (insightflow_core's PLANNER_DIMENSION_FIELDS) -- grouping an answer by a
    # raw timestamp or an amount produces one row per distinct value, not a breakdown.
    all_field_names = sorted(
        {f.name for e in semantic_model.entities for f in e.fields if planner_dimension_problem(f.name) is None}
    )
    dimensions = []
    for name in all_field_names:
        try:
            field_resolver.find_entity_for_field(name)
        except (KeyError, ValueError):
            continue
        dimensions.append(name)

    # Only metrics this dataset can actually compute (see insightflow_core's
    # metric_resolvability.py). A question about anything else should come back from the planner
    # as unanswerable -- the explicit "can't be answered from available data" refusal POC 4 exists
    # to give -- rather than as a plan that crashes in the engine. QuestionValidator re-checks
    # the assembled query regardless, same double-checking as the dimension filter above.
    runnable = resolvable_metric_names(registry, semantic_model)
    metrics = [
        # The bound entity, not `measure.entity` -- that's an optional pin and usually unset. Every
        # runnable bare measure binds on its own, so bind_measure can't raise here.
        MetricSummary(name=name, kind="measure", description=f"raw measure on {bind_measure(measure, semantic_model).entity}")
        for name, measure in registry.measures.items()
        if name in runnable
    ] + [
        MetricSummary(name=name, kind=metric.kind.value, description=metric.description)
        for name, metric in registry.metrics.items()
        if isinstance(metric, MetricDefinition) and name in runnable
    ]
    groupable_metrics = [m.name for m in metrics if m.kind in ("measure", "base")]
    growth_metrics = [m.name for m in metrics if m.kind == MetricKind.GROWTH.value]

    return PlannerContext(
        entities=entities,
        available_dimensions=dimensions,
        available_metrics=metrics,
        groupable_metrics=groupable_metrics,
        growth_metrics=growth_metrics,
        supported_time_expressions=list(TimeExpression),
    )
