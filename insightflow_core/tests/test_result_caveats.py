"""Deterministic caveats on correct-but-misleading results (models/compiled_query.py's CaveatRule).

Found on real Olist: every customer_id there appears on exactly one order (it's per-order; the person
is customer_unique_id), so repeat_purchase_rate was a hard 0 against a true 3.12%, and a dashboard
narrative presented that 0 as a business finding.
"""
from insightflow_core.compilation import FieldResolver, SQLCompiler
from insightflow_core.execution import QueryExecutor
from insightflow_core.models import (
    AggregationType,
    AnalyticalQuery,
    Entity,
    HavingClause,
    MetricDefinition,
    MetricKind,
    OperationType,
    SemanticField,
    SemanticModel,
)
from insightflow_core.models.registry import Measure
from insightflow_core.pipeline import AnalyticsEnginePipeline
from insightflow_core.registry import MetricRegistry
from insightflow_core.safety import SQLSafetyChecker
from insightflow_core.validation import ASTValidator


def _run(tmp_path, orders_csv: str):
    (tmp_path / "orders.csv").write_text(orders_csv)
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
    registry = MetricRegistry()
    registry.register_measure(Measure(name="orders_per_customer", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_measure(Measure(name="customers", source_field="customer_id", aggregation=AggregationType.COUNT_DISTINCT))
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
    executor = QueryExecutor(":memory:", 10, 1000)
    executor.register_sources(model, str(tmp_path))
    engine = AnalyticsEnginePipeline(
        registry=registry,
        validator=ASTValidator(registry, model, 1000),
        compiler=SQLCompiler(registry, FieldResolver(model), 1000),
        checker=SQLSafetyChecker(model, 1000),
        executor=executor,
    )
    return engine.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="repeat_purchase_rate"))


def test_a_rate_no_group_can_reach_is_caveated_not_just_reported_as_zero(tmp_path):
    # Olist's shape: a distinct customer_id on every order.
    result = _run(tmp_path, "order_id,customer_id\no1,c1\no2,c2\no3,c3\n")
    assert result.value == 0
    assert len(result.caveats) == 1 and "0 by construction" in result.caveats[0] and '"customer_id"' in result.caveats[0]


def test_a_genuine_rate_carries_no_caveat(tmp_path):
    result = _run(tmp_path, "order_id,customer_id\no1,c1\no2,c1\no3,c2\n")
    assert result.value == 0.5
    assert result.caveats == []
