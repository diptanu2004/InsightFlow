"""Time filters and growth on a measure whose own entity has no date -- the date is one relationship
away (compilation/measure_binding.py's resolve_time_entity, SQLCompiler._time_filter_predicate).

Found on a real Olist upload: revenue lives on payments, the purchase date on the orders header, so
"revenue last quarter" and revenue_growth were refused. The fixture keeps that shape.
"""
from datetime import date

import pytest

from insightflow_core.compilation import FieldResolver, SQLCompiler
from insightflow_core.compilation.measure_binding import UnresolvableTimeField, resolve_time_entity
from insightflow_core.execution import QueryExecutor
from insightflow_core.models import (
    AggregationType,
    AnalyticalQuery,
    Entity,
    MetricDefinition,
    MetricKind,
    OperationType,
    Relationship,
    SemanticField,
    SemanticModel,
    TimeFilter,
)
from insightflow_core.models.query import GrowthSpec
from insightflow_core.models.registry import Measure
from insightflow_core.pipeline import AnalyticsEnginePipeline
from insightflow_core.registry import MetricRegistry
from insightflow_core.safety import SQLSafetyChecker
from insightflow_core.validation import ASTValidator

ORDERS, PAYMENTS = "orders_header", "payments"


def _field(name, column, file):
    return SemanticField(name=name, source_column=column, source_file=file, confidence=0.99)


def _model(relationships=True) -> SemanticModel:
    return SemanticModel(
        entities=[
            Entity(name=ORDERS, fields=[_field("order_id", "order_id", ORDERS), _field("transaction_date", "purchased_at", ORDERS)]),
            Entity(name=PAYMENTS, fields=[_field("order_id", "order_id", PAYMENTS), _field("revenue", "payment_value", PAYMENTS)]),
        ],
        relationships=(
            [Relationship(from_field=f"{PAYMENTS}.order_id", to_field=f"{ORDERS}.order_id", confidence=1.0, direction="x")]
            if relationships
            else []
        ),
    )


def _registry() -> MetricRegistry:
    registry = MetricRegistry()
    registry.register_measure(Measure(name="revenue", source_field="revenue", aggregation=AggregationType.SUM))
    registry.register_metric(MetricDefinition(name="revenue_growth", kind=MetricKind.GROWTH, base_measure="revenue"))
    return registry


def _engine(tmp_path, model, registry):
    # Order A (Jan) paid in two installments; B (Feb) one payment; C (Mar) one payment.
    # One timestamp format throughout, like real Olist -- mixing bare dates and timestamps in a column
    # makes DuckDB infer it as text.
    (tmp_path / f"{ORDERS}.csv").write_text(
        "order_id,purchased_at\nA,2024-01-10 09:00:00\nB,2024-02-15 23:30:00\nC,2024-03-05 12:00:00\n"
    )
    (tmp_path / f"{PAYMENTS}.csv").write_text("order_id,payment_value\nA,100\nA,50\nB,30\nC,400\n")
    executor = QueryExecutor(":memory:", 10, 1000)
    executor.register_sources(model, str(tmp_path))
    return AnalyticsEnginePipeline(
        registry=registry,
        validator=ASTValidator(registry, model, 1000),
        compiler=SQLCompiler(registry, FieldResolver(model), 1000),
        checker=SQLSafetyChecker(model, 1000),
        executor=executor,
    )


def test_revenue_in_a_period_counts_only_payments_for_orders_placed_in_it(tmp_path):
    model, registry = _model(), _registry()
    engine = _engine(tmp_path, model, registry)

    jan_feb = engine.run(
        AnalyticalQuery(
            operation=OperationType.AGGREGATE,
            metric="revenue",
            time_filter=TimeFilter(start_date=date(2024, 1, 1), end_date=date(2024, 2, 15)),
        )
    )

    # A's two installments (150) + B (30, placed late on the end_date day, which must count).
    # A semi-join keeps both of A's payment rows exactly once; C is outside the window.
    assert jan_feb.value == 180
    assert "IN (SELECT" in jan_feb.metadata.sql


def test_growth_compares_periods_through_the_related_date(tmp_path):
    model, registry = _model(), _registry()
    engine = _engine(tmp_path, model, registry)

    result = engine.run(
        AnalyticalQuery(
            operation=OperationType.GROWTH,
            metric="revenue_growth",
            growth=GrowthSpec(
                current_period=TimeFilter(start_date=date(2024, 3, 1), end_date=date(2024, 3, 31)),
                comparison_period=TimeFilter(start_date=date(2024, 1, 1), end_date=date(2024, 2, 29)),
            ),
        )
    )

    assert result.value == pytest.approx((400 - 180) / 180)


def test_without_a_relationship_to_a_dated_entity_the_filter_is_refused_not_ignored():
    registry = _registry()
    validator = ASTValidator(registry, _model(relationships=False), 1000)

    result = validator.validate(
        AnalyticalQuery(
            operation=OperationType.AGGREGATE,
            metric="revenue",
            time_filter=TimeFilter(start_date=date(2024, 1, 1), end_date=date(2024, 1, 31)),
        )
    )

    assert [e.code for e in result.errors] == ["time_filter_entity_missing_time_field"]


def test_two_directly_related_dated_entities_are_ambiguous():
    model = _model()
    model.entities.append(
        Entity(name="refunds", fields=[_field("order_id", "order_id", "refunds"), _field("transaction_date", "refunded_at", "refunds")])
    )
    model.relationships.append(
        Relationship(from_field=f"{PAYMENTS}.order_id", to_field="refunds.order_id", confidence=1.0, direction="x")
    )

    with pytest.raises(UnresolvableTimeField, match="more than one directly related entity"):
        resolve_time_entity(PAYMENTS, "transaction_date", model)
