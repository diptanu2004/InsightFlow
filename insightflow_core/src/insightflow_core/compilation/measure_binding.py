"""Binds a metric's measures to concrete entities for one dataset's semantic model.

A Measure names a semantic *field* (`revenue`), optionally pinned to an entity. Which entity that
field actually lives on is a property of the dataset, not of the registry: POC 1 names entities
after source filenames, so the sample data's `revenue` sits on `orders` while the Kaggle Olist
upload's sits on `olist_order_payments_dataset`. This module is the single place that decision is
made, and every consumer -- SQLCompiler, ASTValidator, metric_resolvability -- binds through it, so
"the entity this measure reads" can never mean different things in different layers.

Binding hands back *pinned copies* of each Measure (`entity` filled in), which is what lets the
compiler's existing `measure.entity` reads stay exactly as they were.

The ambiguity rule is deliberately strict, and deterministic -- no LLM is ever involved:
  1. A pinned measure uses its pin, or is unresolvable if the pin isn't in this dataset.
  2. An unpinned measure whose field exists on exactly one entity uses that entity.
  3. A field on several entities is narrowed by the measure's declared population
     (`colocate_with_field`), if it has one: `customers` counts `customer_id` where `order_id`
     lives, i.e. customers who ordered rather than every customer on file. Needed because a key
     column sits on both sides of every relationship, so without it basic count KPIs were refused
     on almost any normalized schema (the sample data itself lost `customers`).
  4. A measure read alongside a sibling (RATIO numerator/denominator, HAVING_RATIO threshold/
     denominator) co-locates: if the sibling's candidates narrow it to one shared entity, both
     use it. Real Olist `order_id` is on both order_items and payments; beside `revenue` (only on
     payments) AOV binds both measures to payments.
  5. Anything still ambiguous is refused, naming the candidates. Picking by confidence was
     rejected because confidence measures how sure POC 1 is of a column's *meaning*, not which
     table has the right grain -- `order_id` scores 99% on both Olist tables.
"""
from dataclasses import dataclass

from insightflow_core.models.registry import Measure, MetricDefinition, MetricKind
from insightflow_core.models.semantic_model import SemanticModel
from insightflow_core.registry import MetricRegistry


class UnresolvableMeasure(ValueError):
    """A ValueError so every existing `except ValueError` boundary (pipeline -> HTTP 422) reports it
    cleanly, instead of it escaping as an unhandled error the way the old KeyError did."""


@dataclass(frozen=True)
class BoundMetric:
    """The pinned measures one metric reads, by role: "base" (a bare measure, BASE, GROWTH),
    "numerator"/"denominator" (RATIO), "threshold"/"denominator" (HAVING_RATIO)."""

    measures: dict[str, Measure]


def _candidates(measure: Measure, semantic_model: SemanticModel) -> list[str]:
    if measure.entity is not None:
        entity = next((e for e in semantic_model.entities if e.name == measure.entity), None)
        if entity is None:
            raise UnresolvableMeasure(
                f'measure "{measure.name}" reads from entity "{measure.entity}", which this dataset does not have'
            )
        if not any(f.name == measure.source_field for f in entity.fields):
            raise UnresolvableMeasure(
                f'measure "{measure.name}" reads field "{measure.source_field}", '
                f'which entity "{measure.entity}" does not have'
            )
        return [measure.entity]
    found = [e.name for e in semantic_model.entities if any(f.name == measure.source_field for f in e.fields)]
    if not found:
        raise UnresolvableMeasure(
            f'measure "{measure.name}" reads field "{measure.source_field}", which no entity in this dataset has'
        )
    if len(found) > 1 and measure.colocate_with_field is not None:
        # The measure's declared population. Narrows only: a field on one entity binds there
        # regardless, and a hint no candidate carries leaves the ambiguity for the caller to refuse.
        narrowed = [
            name
            for name in found
            if any(
                f.name == measure.colocate_with_field
                for e in semantic_model.entities
                if e.name == name
                for f in e.fields
            )
        ]
        if narrowed:
            return narrowed
    return found


def _ambiguous(measure: Measure, candidates: list[str], sibling: Measure | None = None) -> UnresolvableMeasure:
    where = ", ".join(candidates)
    hint = (
        f' (its population hint "{measure.colocate_with_field}" does not narrow that to one)'
        if measure.colocate_with_field
        else ""
    )
    if sibling is None:
        return UnresolvableMeasure(
            f'measure "{measure.name}" reads field "{measure.source_field}", which is on more than one '
            f"entity ({where}){hint}; ambiguous without a sibling measure to co-locate with or an explicit entity pin"
        )
    return UnresolvableMeasure(
        f'measure "{measure.name}" reads field "{measure.source_field}", which is on more than one '
        f'entity ({where}){hint}, and its sibling measure "{sibling.name}" does not narrow that to one'
    )


def _pin(measure: Measure, entity: str) -> Measure:
    return measure.model_copy(update={"entity": entity})


def bind_measure(measure: Measure, semantic_model: SemanticModel) -> Measure:
    candidates = _candidates(measure, semantic_model)
    if len(candidates) > 1:
        raise _ambiguous(measure, candidates)
    return _pin(measure, candidates[0])


def bind_measure_pair(a: Measure, b: Measure, semantic_model: SemanticModel) -> tuple[Measure, Measure]:
    a_candidates = _candidates(a, semantic_model)
    b_candidates = _candidates(b, semantic_model)

    if len(a_candidates) == 1 and len(b_candidates) == 1:
        # Both settled on their own -- they may legitimately differ (RATIO compiles each side as an
        # independent subquery). Callers that need them together check that themselves.
        return _pin(a, a_candidates[0]), _pin(b, b_candidates[0])

    shared = [entity for entity in a_candidates if entity in b_candidates]
    if len(a_candidates) == 1 and a_candidates[0] in b_candidates:
        return _pin(a, a_candidates[0]), _pin(b, a_candidates[0])
    if len(b_candidates) == 1 and b_candidates[0] in a_candidates:
        return _pin(a, b_candidates[0]), _pin(b, b_candidates[0])
    if len(shared) == 1:
        return _pin(a, shared[0]), _pin(b, shared[0])

    # Report whichever side is actually ambiguous (a side with one candidate isn't the problem).
    if len(a_candidates) > 1:
        raise _ambiguous(a, a_candidates, sibling=b)
    raise _ambiguous(b, b_candidates, sibling=a)


def _registered_measure(name: str, registry: MetricRegistry) -> Measure:
    if name not in registry.measures:
        raise UnresolvableMeasure(f'"{name}" is not a registered measure')
    return registry.measures[name]


def bind_metric(
    resolved: Measure | MetricDefinition,
    registry: MetricRegistry,
    semantic_model: SemanticModel,
    having_field: str | None = None,
) -> BoundMetric:
    """Raises UnresolvableMeasure. `having_field` overrides a HAVING_RATIO's default threshold
    measure, the way `AnalyticalQuery.having_override` does at compile time."""
    if isinstance(resolved, Measure):
        return BoundMetric({"base": bind_measure(resolved, semantic_model)})

    if resolved.kind in (MetricKind.BASE, MetricKind.GROWTH):
        base = _registered_measure(resolved.base_measure, registry)
        return BoundMetric({"base": bind_measure(base, semantic_model)})

    if resolved.kind == MetricKind.RATIO:
        numerator, denominator = bind_measure_pair(
            _registered_measure(resolved.numerator_measure, registry),
            _registered_measure(resolved.denominator_measure, registry),
            semantic_model,
        )
        return BoundMetric({"numerator": numerator, "denominator": denominator})

    if resolved.kind == MetricKind.HAVING_RATIO:
        threshold, denominator = bind_measure_pair(
            _registered_measure(having_field or resolved.having.field, registry),
            _registered_measure(resolved.denominator_measure, registry),
            semantic_model,
        )
        return BoundMetric({"threshold": threshold, "denominator": denominator})

    raise ValueError(f"unhandled MetricKind: {resolved.kind}")
