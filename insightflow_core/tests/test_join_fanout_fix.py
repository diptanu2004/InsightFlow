"""Regression test for a real bug found via a real Groq call against real Olist data, run from
poc3_dashboard_generation (docs/real_world_integration_test.md, "Finding 4") — never hit by any
fixture here or in the sample dataset because both existing cross-entity GROUP BY tests in
test_engine_integration.py (`test_category_performance_group_by`, `test_regional_performance_...`)
join in the SAFE direction: the measure's own entity ("orders") holds the FK, and the dimension's
entity ("products"/"customers") is on the "one" side, so the join never fans out `orders`' rows.

This test reproduces the UNSAFE direction: the measure's entity ("payments", one row per order) is
on the "one" side, and the dimension's entity ("order_items", one-or-more rows per order) is on
the "many" side. Grouping `revenue` (SUM over `payments.amount`) by `price` (on `order_items`) used
to multiply a payment's amount by however many order_items rows shared its order_id, because the
plain `JOIN ... GROUP BY` SQLCompiler used to emit produces one row per matching order_items row,
not per payments row. The real Olist repro: an order with 3 line items at the same price had its
payment value counted 3x in that price's SUM (729.39 true total -> 1,556.97 computed).
"""
import json
from pathlib import Path

import pytest

from insightflow_core.models import (
    AggregationType,
    AnalyticalQuery,
    Entity,
    OperationType,
    Relationship,
    SemanticField,
    SemanticModel,
    SortSpec,
)
from insightflow_core.models.registry import Measure
from insightflow_core.pipeline import build_pipeline
from insightflow_core.registry import MetricRegistry


def _write_csv(path: Path, header: str, rows: list[str]) -> None:
    path.write_text(header + "\n" + "\n".join(rows) + "\n")


def _fanout_semantic_model() -> SemanticModel:
    return SemanticModel(
        entities=[
            Entity(
                name="payments",
                fields=[
                    SemanticField(name="order_id", source_column="order_id", source_file="payments", confidence=0.99),
                    SemanticField(name="revenue", source_column="amount", source_file="payments", confidence=0.99),
                ],
            ),
            Entity(
                name="order_items",
                fields=[
                    SemanticField(name="order_id", source_column="order_id", source_file="order_items", confidence=0.99),
                    SemanticField(name="price", source_column="price", source_file="order_items", confidence=0.99),
                ],
            ),
        ],
        relationships=[
            Relationship(
                from_field="order_items.order_id",
                to_field="payments.order_id",
                confidence=1.0,
                direction="order_items.order_id -> payments.order_id",
            ),
        ],
    )


@pytest.fixture()
def fanout_pipeline(tmp_path):
    # payments: exactly one row per order (like the real Olist repro).
    # order 1: 3 order_items rows, all price=10   -- pure fan-out, must count once (100.0).
    # order 2: 1 order_items row,  price=20       -- no fan-out at all (50.0).
    # order 3: 2 order_items rows, both price=20  -- fan-out into the SAME group order 2 also
    #                                                 uses; must still count once (30.0), not
    #                                                 merge with order 2's row (different order,
    #                                                 same price+row-shape risk the old join-key-
    #                                                 based DISTINCT design would have mis-handled).
    _write_csv(tmp_path / "payments.csv", "order_id,amount", ["1,100.0", "2,50.0", "3,30.0"])
    _write_csv(
        tmp_path / "order_items.csv",
        "order_id,price",
        ["1,10", "1,10", "1,10", "2,20", "3,20", "3,20"],
    )
    registry = MetricRegistry()
    registry.register_measure(Measure(name="revenue", entity="payments", source_field="revenue", aggregation=AggregationType.SUM))
    return build_pipeline(_fanout_semantic_model(), registry, str(tmp_path))


def test_group_by_across_a_one_to_many_join_does_not_fan_out_sum(fanout_pipeline):
    """Old bug: price=10 would sum to 300.0 (order 1's 100.0 counted once per matching
    order_items row) and price=20 would sum to 110.0 (order 3's 30.0 counted twice) — total
    410.0 against a true total of 180.0. Fixed: each order's payment is attributed to a price
    group at most once per distinct price it touches; here every order touches exactly one price,
    so the grouped sums now reconcile exactly to the ungrouped total."""
    query = AnalyticalQuery(
        operation=OperationType.GROUP_BY,
        metric="revenue",
        dimension="price",
        sort=SortSpec(field="price", direction="asc"),
    )
    result = fanout_pipeline.run(query)
    assert result.rows == [
        {"price": 10.0, "value": 100.0},
        {"price": 20.0, "value": 80.0},
    ]
    assert sum(row["value"] for row in result.rows) == pytest.approx(180.0)

    total = fanout_pipeline.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="revenue"))
    assert total.value == pytest.approx(180.0)


def test_group_by_still_attributes_a_multi_price_order_once_per_distinct_price(tmp_path):
    """Documented, defensible residual limitation (not the same failure as the fan-out bug): an
    order whose line items span more than one distinct dimension value is counted once per
    distinct value it touches, so cross-group sums don't reconcile to the grand total in that
    specific case -- see docs/real_world_integration_test.md's "Finding 4" candidate-fix section.
    This locks in that documented behavior so it doesn't silently change later."""
    _write_csv(tmp_path / "payments.csv", "order_id,amount", ["1,40.0"])
    _write_csv(tmp_path / "order_items.csv", "order_id,price", ["1,10", "1,30"])
    registry = MetricRegistry()
    registry.register_measure(Measure(name="revenue", entity="payments", source_field="revenue", aggregation=AggregationType.SUM))
    pipeline = build_pipeline(_fanout_semantic_model(), registry, str(tmp_path))

    query = AnalyticalQuery(
        operation=OperationType.GROUP_BY,
        metric="revenue",
        dimension="price",
        sort=SortSpec(field="price", direction="asc"),
    )
    result = pipeline.run(query)
    assert result.rows == [
        {"price": 10.0, "value": 40.0},
        {"price": 30.0, "value": 40.0},
    ]
