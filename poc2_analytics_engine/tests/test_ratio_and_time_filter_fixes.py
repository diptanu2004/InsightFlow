"""Regression tests for two real bugs found only by testing against the full-scale real Olist
dataset (docs/real_world_integration_test.md, "Findings from the real-scale test") — both were
structurally impossible to hit with the existing sample dataset in data/ (see below), which is
exactly why they survived past 45 previously-passing tests.

1. Cross-entity RATIO used to JOIN numerator_entity to denominator_entity and aggregate both
   sides over that one joined row set (SQLCompiler._compile_ratio). A JOIN drops any row with no
   counterpart on the other side from BOTH aggregates at once. data/'s own "aov" (revenue/orders)
   never exercised this: bootstrap_registry() (src/insightflow_analytics/registry/bootstrap.py) puts both
   "revenue" and "orders" on the SAME "orders" entity, so _compile_ratio's same-entity branch
   never even builds a join. The real Olist registry needs revenue on `payments` and orders on
   `orders` -- genuinely different entities -- and the real data has one real "delivered" order
   with zero matching payment rows, which the join silently dropped from the denominator.

2. Time filters used `col BETWEEN $start AND $end` where TimeFilter.end_date (models/query.py)
   is a calendar `date`, not a timestamp -- binding `end_date` as literal midnight silently
   excludes every row timestamped later that same day. data/raw/sample's growth test periods
   happened not to have any row land on a range boundary, so it never showed up there either.
"""
import json
from pathlib import Path

import pytest

from insightflow_core.models import (
    AggregationType,
    AnalyticalQuery,
    Entity,
    GrowthSpec,
    MetricDefinition,
    MetricKind,
    OperationType,
    Relationship,
    SemanticField,
    SemanticModel,
    TimeFilter,
)
from insightflow_core.models.registry import Measure
from insightflow_core.pipeline import build_pipeline
from insightflow_analytics.registry import MetricRegistry


def _write_csv(path: Path, header: str, rows: list[str]) -> None:
    path.write_text(header + "\n" + "\n".join(rows) + "\n")


@pytest.fixture()
def cross_entity_ratio_pipeline(tmp_path):
    # "orders": 3 real orders. "payments": only 2 of them have a payment row -- order 3 is a
    # real-shaped gap (present, no matching detail row), exactly like the real Olist order that
    # exposed this.
    _write_csv(tmp_path / "orders.csv", "order_id", ["1", "2", "3"])
    _write_csv(tmp_path / "payments.csv", "order_id,amount", ["1,10.0", "2,20.0"])

    semantic_model = SemanticModel(
        entities=[
            Entity(name="orders", fields=[SemanticField(name="order_id", source_column="order_id", source_file="orders", confidence=0.99)]),
            Entity(
                name="payments",
                fields=[
                    SemanticField(name="order_id", source_column="order_id", source_file="payments", confidence=0.99),
                    SemanticField(name="revenue", source_column="amount", source_file="payments", confidence=0.99),
                ],
            ),
        ],
        relationships=[
            Relationship(from_field="orders.order_id", to_field="payments.order_id", confidence=1.0, direction="orders.order_id -> payments.order_id"),
        ],
    )
    registry = MetricRegistry()
    registry.register_measure(Measure(name="revenue", entity="payments", source_field="revenue", aggregation=AggregationType.SUM))
    registry.register_measure(Measure(name="orders", entity="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_metric(MetricDefinition(name="aov", kind=MetricKind.RATIO, numerator_measure="revenue", denominator_measure="orders"))
    return build_pipeline(semantic_model, registry, str(tmp_path))


def test_cross_entity_ratio_denominator_is_not_corrupted_by_the_join(cross_entity_ratio_pipeline):
    """revenue=30 over 3 real orders (one with no payment row) must be 10.0, not 15.0 (30/2 --
    the old bug: the order missing a payment silently vanished from the denominator too)."""
    result = cross_entity_ratio_pipeline.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="aov"))
    assert result.value == pytest.approx(10.0)


@pytest.fixture()
def time_filter_pipeline(tmp_path):
    # One order late in the day on the last day of the range (2024-01-31 23:00:00) -- an
    # inclusive "through 2024-01-31" filter must count it.
    _write_csv(
        tmp_path / "orders.csv",
        "order_id,transaction_date",
        ["1,2024-01-31 23:00:00", "2,2024-02-01 00:00:01"],
    )
    semantic_model = SemanticModel(
        entities=[
            Entity(
                name="orders",
                fields=[
                    SemanticField(name="order_id", source_column="order_id", source_file="orders", confidence=0.99),
                    SemanticField(name="transaction_date", source_column="transaction_date", source_file="orders", confidence=0.99),
                ],
            )
        ],
        relationships=[],
    )
    registry = MetricRegistry()
    registry.register_measure(Measure(name="orders", entity="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT))
    return build_pipeline(semantic_model, registry, str(tmp_path))


def test_time_filter_end_date_includes_the_whole_last_day(time_filter_pipeline):
    query = AnalyticalQuery(
        operation=OperationType.AGGREGATE,
        metric="orders",
        time_filter=TimeFilter(start_date="2024-01-01", end_date="2024-01-31"),
    )
    result = time_filter_pipeline.run(query)
    # order 1 (23:00:00 on the last day) must count; order 2 (next day) must not.
    assert result.value == pytest.approx(1.0)


def test_growth_period_end_date_includes_the_whole_last_day(time_filter_pipeline):
    growth = GrowthSpec(
        current_period=TimeFilter(start_date="2024-02-01", end_date="2024-02-28"),
        comparison_period=TimeFilter(start_date="2024-01-01", end_date="2024-01-31"),
    )
    time_filter_pipeline.registry.register_metric(
        MetricDefinition(name="order_growth", kind=MetricKind.GROWTH, base_measure="orders")
    )
    result = time_filter_pipeline.run(AnalyticalQuery(operation=OperationType.GROWTH, metric="order_growth", growth=growth))
    # comparison period (Jan, inclusive) sees order 1 -> 1; current period (Feb) sees order 2 -> 1.
    # growth = (1 - 1) / 1 = 0.0 -- the point is both periods count their boundary-day order at
    # all (old bug: comparison period would see 0, raising ZeroDivisionError-shaped NULL/None).
    assert result.value == pytest.approx(0.0)
