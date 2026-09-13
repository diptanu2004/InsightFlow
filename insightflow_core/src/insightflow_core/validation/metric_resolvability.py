"""Can a registered metric actually run against *this* dataset's semantic model?

Registration and resolvability are different questions. `MetricRegistry.is_registered` only knows a
name was configured; whether that metric can compile depends on the dataset -- whether the entity
and field each of its measures points at exist in this semantic model, and, for GROWTH, whether
that entity carries a time field to compare periods on.

Found in Phase 8 M4, when the first real user upload went through the real API: every registered
measure named `entity="orders"`, but POC 1 names entities after source *filenames*, so the Kaggle
Olist files produced `olist_order_payments_dataset` and friends. Registered-but-unresolvable metrics
passed ASTValidator and surfaced as an unhandled KeyError inside FieldResolver (a raw 500 from the
backend), and both LLM planners were offered metrics that could never run.

Each rule below mirrors a requirement SQLCompiler already enforces for that metric kind, rather
than inventing new ones -- "resolvable" means exactly "the compiler will find everything it looks
up". Per-query concerns (a GROUP_BY dimension's join path, a time_filter) stay in ASTValidator;
this module only answers whether the metric itself can ever run on this dataset.
"""
from insightflow_core.compilation.field_resolver import FieldResolver
from insightflow_core.compilation.sql_compiler import SQLCompiler
from insightflow_core.models.registry import Measure, MetricKind
from insightflow_core.models.semantic_model import Entity, SemanticModel
from insightflow_core.registry import MetricRegistry


def _find_entity(semantic_model: SemanticModel, name: str) -> Entity | None:
    return next((entity for entity in semantic_model.entities if entity.name == name), None)


def _measure_problem(measure: Measure, semantic_model: SemanticModel) -> str | None:
    # SQLCompiler._measure_sql -> FieldResolver.resolve_field(measure.entity, measure.source_field)
    entity = _find_entity(semantic_model, measure.entity)
    if entity is None:
        return f'measure "{measure.name}" reads from entity "{measure.entity}", which this dataset does not have'
    if not any(field.name == measure.source_field for field in entity.fields):
        return (
            f'measure "{measure.name}" reads field "{measure.source_field}", '
            f'which entity "{measure.entity}" does not have'
        )
    return None


def _named_measure_problem(name: str, registry: MetricRegistry, semantic_model: SemanticModel) -> str | None:
    if name not in registry.measures:
        return f'"{name}" is not a registered measure'
    return _measure_problem(registry.measures[name], semantic_model)


def unresolvable_reason(name: str, registry: MetricRegistry, semantic_model: SemanticModel) -> str | None:
    """None if `name` can run against `semantic_model`; otherwise a human-readable reason why not."""
    if not registry.is_registered(name):
        return f'"{name}" is not a registered measure or metric'

    resolved = registry.resolve(name)
    if isinstance(resolved, Measure):
        return _measure_problem(resolved, semantic_model)

    if resolved.kind == MetricKind.BASE:
        return _named_measure_problem(resolved.base_measure, registry, semantic_model)

    if resolved.kind == MetricKind.RATIO:
        # _compile_ratio already handles numerator and denominator on different entities (two
        # independent scalar subqueries), so each only has to resolve on its own.
        return _named_measure_problem(resolved.numerator_measure, registry, semantic_model) or _named_measure_problem(
            resolved.denominator_measure, registry, semantic_model
        )

    if resolved.kind == MetricKind.GROWTH:
        problem = _named_measure_problem(resolved.base_measure, registry, semantic_model)
        if problem:
            return problem
        base = registry.measures[resolved.base_measure]
        entity = _find_entity(semantic_model, base.entity)
        if not any(field.name == SQLCompiler.TIME_FIELD for field in entity.fields):
            return (
                f'metric "{name}" compares periods on "{SQLCompiler.TIME_FIELD}", which entity '
                f'"{base.entity}" does not have'
            )
        return None

    # HAVING_RATIO -- mirrors SQLCompiler._compile_having_ratio's lookups step by step.
    threshold_name = resolved.having.field
    problem = _named_measure_problem(threshold_name, registry, semantic_model) or _named_measure_problem(
        resolved.denominator_measure, registry, semantic_model
    )
    if problem:
        return problem
    threshold = registry.measures[threshold_name]
    denominator = registry.measures[resolved.denominator_measure]
    if threshold.entity != denominator.entity:
        return f'metric "{name}" needs its threshold and denominator measures on one entity'

    group_entity_name = resolved.group_by_entity or threshold.entity
    group_entity = _find_entity(semantic_model, group_entity_name)
    if group_entity is None:
        return f'metric "{name}" groups by entity "{group_entity_name}", which this dataset does not have'
    grouping_fields = [
        field
        for field in group_entity.fields
        if field.name == resolved.group_by_field
        and (resolved.group_by_source_column is None or field.source_column == resolved.group_by_source_column)
    ]
    if not grouping_fields:
        pinned = f' (source column "{resolved.group_by_source_column}")' if resolved.group_by_source_column else ""
        return (
            f'metric "{name}" groups by field "{resolved.group_by_field}"{pinned}, '
            f'which entity "{group_entity_name}" does not have'
        )
    if group_entity_name != threshold.entity and FieldResolver(semantic_model).resolve_join_path(
        threshold.entity, group_entity_name
    ) is None:
        return f'metric "{name}" needs a relationship between "{threshold.entity}" and "{group_entity_name}"'
    return None


def resolvable_metric_names(registry: MetricRegistry, semantic_model: SemanticModel) -> set[str]:
    names = set(registry.measures) | set(registry.metrics)
    return {name for name in names if unresolvable_reason(name, registry, semantic_model) is None}
