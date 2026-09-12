"""Regression test for a real finding from POC 3's full-scale real-Olist run
(poc3_dashboard_generation/docs/real_world_integration_test.md) — `repeat_purchase_rate` silently
computed exactly 0.0 against ~99.4k real orders. Root cause: `group_by_field="customer_id"`
resolved, by default, in the SAME entity as the numerator/denominator measures ("orders") — but
real Olist `orders` rows carry only a per-ORDER surrogate `customer_id`, never the real per-PERSON
`customer_unique_id` (that only exists on the `customers` entity, and even there POC 1's real
mapping collapses both physical columns under the one canonical name "customer_id", so
`FieldResolver`'s highest-confidence tie-break can't reach it either — see its docstring's second
implementation note). Grouping by a column that is unique per order makes every group size 1, so
"repeat" is structurally always false: a mechanically-correct answer to the wrong grouping key,
not a compiler bug in the arithmetic itself.

This test reproduces the exact shape with a small synthetic fixture: 5 orders, each with its own
unique surrogate `customer_id` (like real Olist), joining to a `customers` entity where the real
`customer_unique_id` repeats for two of the five orders' customers -- true repeat customers that
the surrogate-keyed grouping can never see.
"""
from pathlib import Path

import pytest

from insightflow_core.models import (
    AggregationType,
    AnalyticalQuery,
    Entity,
    HavingClause,
    OperationType,
    Relationship,
    SemanticField,
    SemanticModel,
)
from insightflow_core.models.registry import MetricDefinition, MetricKind
from insightflow_core.models.registry import Measure
from insightflow_core.pipeline import build_pipeline
from insightflow_core.registry import MetricRegistry


def _write_csv(path: Path, header: str, rows: list[str]) -> None:
    path.write_text(header + "\n" + "\n".join(rows) + "\n")


def _customer_id_conflation_semantic_model() -> SemanticModel:
    return SemanticModel(
        entities=[
            Entity(
                name="orders",
                fields=[
                    SemanticField(name="order_id", source_column="order_id", source_file="orders", confidence=0.99),
                    SemanticField(name="customer_id", source_column="customer_id", source_file="orders", confidence=0.99),
                ],
            ),
            Entity(
                name="customers",
                fields=[
                    # Same real-world conflation as POC 1's actual Olist mapping: both the
                    # per-order surrogate AND the real per-person id map to one canonical name.
                    SemanticField(name="customer_id", source_column="customer_id", source_file="customers", confidence=0.99),
                    SemanticField(name="customer_id", source_column="customer_unique_id", source_file="customers", confidence=0.95),
                ],
            ),
        ],
        relationships=[
            Relationship(
                from_field="orders.customer_id",
                to_field="customers.customer_id",
                confidence=1.0,
                direction="orders.customer_id -> customers.customer_id",
            ),
        ],
    )


def _build_registry(*, fixed: bool) -> MetricRegistry:
    registry = MetricRegistry()
    registry.register_measure(Measure(name="orders_per_customer", entity="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_measure(Measure(name="customers", entity="orders", source_field="customer_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_metric(
        MetricDefinition(
            name="repeat_purchase_rate",
            kind=MetricKind.HAVING_RATIO,
            numerator_measure="orders_per_customer",
            denominator_measure="customers",
            group_by_field="customer_id",
            having=HavingClause(field="orders_per_customer", operator=">=", value=2),
            group_by_entity="customers" if fixed else None,
            group_by_source_column="customer_unique_id" if fixed else None,
        )
    )
    return registry


@pytest.fixture()
def conflation_data(tmp_path):
    # 5 orders, each its own surrogate customer_id (like real Olist -- never repeats on `orders`).
    _write_csv(
        tmp_path / "orders.csv",
        "order_id,customer_id",
        ["1,c1", "2,c2", "3,c3", "4,c4", "5,c5"],
    )
    # Real identity: u1 places orders 1 & 2 (repeat), u2 places order 3 (one-time), u3 places
    # orders 4 & 5 (repeat) -- 2 of 3 real customers are repeat buyers (rate = 2/3).
    _write_csv(
        tmp_path / "customers.csv",
        "customer_id,customer_unique_id",
        ["c1,u1", "c2,u1", "c3,u2", "c4,u3", "c5,u3"],
    )
    return tmp_path


def test_grouping_by_the_surrogate_customer_id_silently_reports_zero_repeat_purchases(conflation_data):
    """Locks in the bug's exact shape, unfixed: every order has its own never-repeating surrogate
    customer_id, so every group has size 1 and the "≥2 orders" count is always 0 -- a
    mechanically-correct answer to the wrong grouping key, matching the full-scale run's observed
    repeat_purchase_kpi = 0.0 exactly."""
    registry = _build_registry(fixed=False)
    pipeline = build_pipeline(_customer_id_conflation_semantic_model(), registry, str(conflation_data))

    result = pipeline.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="repeat_purchase_rate"))

    assert result.value == pytest.approx(0.0)


def test_group_by_entity_and_source_column_reach_the_real_repeat_customers(conflation_data):
    """Fixed: group_by_entity="customers" (join required) + group_by_source_column=
    "customer_unique_id" (bypass FieldResolver's highest-confidence tie-break, which would
    otherwise still pick the surrogate "customer_id" physical column even inside the `customers`
    entity) together reach the real per-person identity. 2 of the 3 real customers (u1, u3) have
    2 orders each -- repeat_purchase_rate = 2/3, not 0.0."""
    registry = _build_registry(fixed=True)
    pipeline = build_pipeline(_customer_id_conflation_semantic_model(), registry, str(conflation_data))

    result = pipeline.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="repeat_purchase_rate"))

    assert result.value == pytest.approx(2.0 / 3.0)


def test_fix_is_a_no_op_when_group_by_field_already_lives_in_the_measures_entity(tmp_path):
    """The existing same-entity HAVING_RATIO path (test_engine_integration.py's
    test_repeat_purchase_rate_having_ratio) must stay byte-identical: this fix only changes
    behavior when group_by_entity is explicitly set to a DIFFERENT entity."""
    _write_csv(tmp_path / "orders.csv", "order_id,customer_id", ["1,c1", "2,c1", "3,c2"])
    registry = MetricRegistry()
    registry.register_measure(Measure(name="orders_per_customer", entity="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_measure(Measure(name="customers", entity="orders", source_field="customer_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_metric(
        MetricDefinition(
            name="repeat_purchase_rate",
            kind=MetricKind.HAVING_RATIO,
            numerator_measure="orders_per_customer",
            denominator_measure="customers",
            group_by_field="customer_id",
            having=HavingClause(field="orders_per_customer", operator=">=", value=2),
        )
    )
    model = SemanticModel(
        entities=[
            Entity(
                name="orders",
                fields=[
                    SemanticField(name="order_id", source_column="order_id", source_file="orders", confidence=0.99),
                    SemanticField(name="customer_id", source_column="customer_id", source_file="orders", confidence=0.99),
                ],
            )
        ],
        relationships=[],
    )
    pipeline = build_pipeline(model, registry, str(tmp_path))

    result = pipeline.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="repeat_purchase_rate"))

    assert result.value == pytest.approx(0.5)  # c1 has 2 orders, c2 has 1 -> 1 of 2 customers repeat
