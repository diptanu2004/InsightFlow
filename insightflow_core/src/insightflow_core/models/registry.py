"""Metric Registry data shapes — the two-tier design from hld.md's "Metric Registry — two-tier
structure" section. MetricRegistry itself (the store with register()/resolve() methods) lives in
insightflow_core.registry.metric_registry, not here — this module is just the data classes it holds.
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, model_validator

from insightflow_core.models.query import HavingClause


class AggregationType(str, Enum):
    SUM = "sum"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


class Measure(BaseModel):
    """A Tier 1 measure: a raw, aggregatable field drawn from the Semantic Model."""

    name: str
    entity: str
    source_field: str
    aggregation: AggregationType


class MetricKind(str, Enum):
    BASE = "base"
    RATIO = "ratio"
    GROWTH = "growth"
    HAVING_RATIO = "having_ratio"


class MetricDefinition(BaseModel):
    """A Tier 2 named metric: a fixed composition of Tier 1 measures + operators.

    Deliberately a closed set of four `kind`s, not a generic expression tree — see
    class_diagram.md's notes for why. Which fields are required depends on `kind`:
      BASE          -> base_measure
      RATIO         -> numerator_measure, denominator_measure
      GROWTH        -> base_measure (the query supplies the two time windows via `growth`)
      HAVING_RATIO  -> numerator_measure, denominator_measure, having, group_by_field

    `group_by_field` (HAVING_RATIO only) was added while filling in SQLCompiler's stub: the
    original sketch had no way to say *what* a HAVING_RATIO groups by (e.g. "repeat purchase
    rate" groups by customer_id, not by whatever `having.field` happens to name) — see
    class_diagram.md's "Decisions made during implementation" note.

    `group_by_entity`/`group_by_source_column` (HAVING_RATIO only, both optional) were added after
    the full-scale real-Olist run found `repeat_purchase_rate` silently computing 0.0 against real
    data. Root cause: `group_by_field="customer_id"` resolves, by default, in the SAME entity as
    the numerator/denominator measures (here, `orders`) — but real Olist `orders` rows carry only
    a per-ORDER surrogate `customer_id`, never the real per-PERSON `customer_unique_id` (that only
    exists on the `customers` entity, and POC 1's real mapping collapses both `customer_id` and
    `customer_unique_id` under the one canonical name "customer_id" inside `customers`, so even
    `FieldResolver`'s highest-confidence tie-break can't reach it — see its docstring). Grouping by
    a column that is unique per order makes every group size 1, so "repeat" is structurally always
    false: a mechanically-correct answer to the wrong grouping key, not a compiler bug. Setting
    `group_by_entity="customers"` (join required) + `group_by_source_column="customer_unique_id"`
    (bypass the confidence tie-break) together let a HAVING_RATIO metric group by the real
    per-person identity instead. Both default to None — a HAVING_RATIO metric whose grouping field
    lives in its own measures' entity, with no same-entity name collision, needs neither.
    """

    name: str
    kind: MetricKind
    base_measure: Optional[str] = None
    numerator_measure: Optional[str] = None
    denominator_measure: Optional[str] = None
    having: Optional[HavingClause] = None
    group_by_field: Optional[str] = None
    group_by_entity: Optional[str] = None
    group_by_source_column: Optional[str] = None
    description: str = ""

    @model_validator(mode="after")
    def _required_fields_for_kind(self) -> "MetricDefinition":
        if self.kind in (MetricKind.BASE, MetricKind.GROWTH):
            if not self.base_measure:
                raise ValueError(f"kind={self.kind} requires base_measure")
        if self.kind in (MetricKind.RATIO, MetricKind.HAVING_RATIO):
            if not self.numerator_measure or not self.denominator_measure:
                raise ValueError(f"kind={self.kind} requires numerator_measure and denominator_measure")
        if self.kind == MetricKind.HAVING_RATIO:
            if self.having is None:
                raise ValueError("kind=HAVING_RATIO requires a default `having` clause")
            if not self.group_by_field:
                raise ValueError("kind=HAVING_RATIO requires group_by_field")
        elif self.group_by_entity is not None or self.group_by_source_column is not None:
            raise ValueError("group_by_entity/group_by_source_column only apply to kind=HAVING_RATIO")
        return self
