"""Can a registered metric actually run against *this* dataset's semantic model?

Registration and resolvability are different questions. `MetricRegistry.is_registered` only knows a
name was configured; whether that metric can compile depends on the dataset -- whether each of its
measures binds to an entity in this semantic model (compilation/measure_binding.py), and, for GROWTH,
whether that entity carries a time field to compare periods on.

Found in Phase 8 M4, when the first real user upload went through the real API: registered metrics
the dataset couldn't compute passed ASTValidator and surfaced as an unhandled KeyError inside
FieldResolver (a raw 500 from the backend), and both LLM planners were offered metrics that could
never run.

Each rule below mirrors a requirement SQLCompiler already enforces for that metric kind, and every
entity decision goes through the same `bind_metric` the compiler uses -- so "resolvable" means
exactly "the compiler will find everything it looks up", with no second opinion on which entity a
measure reads.
"""
from insightflow_core.compilation.field_resolver import FieldResolver
from insightflow_core.compilation.measure_binding import (
    UnresolvableMeasure,
    UnresolvableTimeField,
    bind_metric,
    resolve_time_entity,
)
from insightflow_core.compilation.sql_compiler import SQLCompiler
from insightflow_core.models.registry import Measure, MetricKind
from insightflow_core.models.semantic_model import Entity, SemanticModel
from insightflow_core.registry import MetricRegistry


def _find_entity(semantic_model: SemanticModel, name: str) -> Entity | None:
    return next((entity for entity in semantic_model.entities if entity.name == name), None)


def unresolvable_reason(name: str, registry: MetricRegistry, semantic_model: SemanticModel) -> str | None:
    """None if `name` can run against `semantic_model`; otherwise a human-readable reason why not."""
    if not registry.is_registered(name):
        return f'"{name}" is not a registered measure or metric'

    resolved = registry.resolve(name)
    try:
        bound = bind_metric(resolved, registry, semantic_model).measures
    except UnresolvableMeasure as exc:
        return str(exc)

    # A bare measure, BASE and RATIO need nothing beyond their measures binding. RATIO sides may
    # land on different entities -- _compile_ratio compiles each as an independent subquery.
    if isinstance(resolved, Measure) or resolved.kind in (MetricKind.BASE, MetricKind.RATIO):
        return None

    if resolved.kind == MetricKind.GROWTH:
        try:
            resolve_time_entity(bound["base"].entity, SQLCompiler.TIME_FIELD, semantic_model)
        except UnresolvableTimeField as exc:
            return f'metric "{name}" can\'t compare periods: {exc}'
        return None

    # HAVING_RATIO -- mirrors SQLCompiler._compile_having_ratio's lookups step by step.
    threshold, denominator = bound["threshold"], bound["denominator"]
    if threshold.entity != denominator.entity:
        return (
            f'metric "{name}" needs its threshold and denominator measures on one entity, but they bind '
            f'to "{threshold.entity}" and "{denominator.entity}"'
        )

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


def join_problem(measure_entity: str, dimension: str, dimension_entity: str, semantic_model: SemanticModel) -> str | None:
    """SQLCompiler._compile_grouped_base joins a measure's entity to its dimension's entity over a
    single-hop relationship, and raises if there isn't one. Multi-hop joins are deliberately
    unsupported (see FieldResolver's docstring)."""
    if dimension_entity == measure_entity:
        return None
    if FieldResolver(semantic_model).resolve_join_path(measure_entity, dimension_entity) is not None:
        return None
    return (
        f'"{dimension}" lives on "{dimension_entity}", which has no direct relationship to "{measure_entity}" '
        "(only single-hop joins are supported)"
    )


def group_by_problem(metric: str, dimension: str, registry: MetricRegistry, semantic_model: SemanticModel) -> str | None:
    """None if a GROUP_BY of `metric` by `dimension` can compile on this dataset. Checks every
    precondition at once, for callers deciding which (metric, dimension) pairs to offer an LLM
    planner; ASTValidator reports the individual failures under their own error codes instead."""
    reason = unresolvable_reason(metric, registry, semantic_model)
    if reason is not None:
        return reason
    resolved = registry.resolve(metric)
    if not isinstance(resolved, Measure) and resolved.kind != MetricKind.BASE:
        return f'metric "{metric}" is kind={resolved.kind.value}; only a bare measure or BASE metric can be grouped'
    try:
        dimension_entity = FieldResolver(semantic_model).find_entity_for_field(dimension)
    except (KeyError, ValueError) as exc:
        return str(exc)
    measure_entity = bind_metric(resolved, registry, semantic_model).measures["base"].entity
    return join_problem(measure_entity, dimension, dimension_entity, semantic_model)
