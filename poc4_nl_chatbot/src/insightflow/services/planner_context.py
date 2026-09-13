"""What QuestionPlanner is allowed to see -- semantic model summary + live registry contents,
never raw source columns or unregistered fields (same information-hiding discipline as POC 1's
profiler-metadata-not-raw-data approach, POC 2's AST never carrying raw column names, and POC 3's
PlannerContext of the same name). Building this is QuestionAnsweringPipeline's job, not the
planner's own -- keeps QuestionPlanner a pure function of (question, context) -> QuestionIntent.
"""
from pydantic import BaseModel, Field

from insightflow_core.compilation import FieldResolver
from insightflow_core.models import MetricDefinition, MetricKind, SemanticModel
from insightflow_core.registry import MetricRegistry

from insightflow.models.time_expression import TimeExpression


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
    all_field_names = sorted({f.name for e in semantic_model.entities for f in e.fields})
    dimensions = []
    for name in all_field_names:
        try:
            field_resolver.find_entity_for_field(name)
        except (KeyError, ValueError):
            continue
        dimensions.append(name)

    metrics = [
        MetricSummary(name=name, kind="measure", description=f"raw measure on {measure.entity}")
        for name, measure in registry.measures.items()
    ] + [
        MetricSummary(name=name, kind=metric.kind.value, description=metric.description)
        for name, metric in registry.metrics.items()
        if isinstance(metric, MetricDefinition)
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
