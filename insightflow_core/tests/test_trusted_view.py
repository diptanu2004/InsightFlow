"""SemanticModel.trusted() -- the only view anything that computes numbers should be built on.

The scenario below is the real one from a 5-file Kaggle Olist upload: POC 1 mapped `freight_value ->
revenue` (40%) and `seller_id -> customer_id` (55%) on order_items, both below its confirmation
threshold. On the raw model, measure binding co-located AOV onto order_items and the engine returned
22.82; the true value is 160.99.
"""
from insightflow_core.compilation.measure_binding import bind_metric
from insightflow_core.models import (
    AggregationType,
    Entity,
    MetricDefinition,
    MetricKind,
    Relationship,
    SemanticField,
    SemanticModel,
)
from insightflow_core.models.registry import Measure
from insightflow_core.registry import MetricRegistry

ORDERS, ITEMS, PAYMENTS, CUSTOMERS = "olist_orders_dataset", "olist_order_items_dataset", "olist_order_payments_dataset", "olist_customers_dataset"


def _field(name, column, file, confidence=0.99, status="auto"):
    return SemanticField(name=name, source_column=column, source_file=file, confidence=confidence, status=status)


def _real_upload() -> SemanticModel:
    return SemanticModel(
        entities=[
            Entity(name=ORDERS, fields=[_field("order_id", "order_id", ORDERS), _field("customer_id", "customer_id", ORDERS)]),
            Entity(
                name=ITEMS,
                fields=[
                    _field("order_id", "order_id", ITEMS),
                    _field("customer_id", "seller_id", ITEMS, 0.55, "needs_confirmation"),
                    _field("revenue", "freight_value", ITEMS, 0.40, "needs_confirmation"),
                ],
            ),
            Entity(name=PAYMENTS, fields=[_field("order_id", "order_id", PAYMENTS), _field("revenue", "payment_value", PAYMENTS, 0.98)]),
            Entity(name=CUSTOMERS, fields=[_field("customer_id", "customer_id", CUSTOMERS)]),
        ],
        relationships=[
            Relationship(from_field=f"{ITEMS}.order_id", to_field=f"{ORDERS}.order_id", confidence=1.0, direction="x"),
            Relationship(from_field=f"{ORDERS}.customer_id", to_field=f"{CUSTOMERS}.customer_id", confidence=1.0, direction="x"),
        ],
    )


def _registry() -> MetricRegistry:
    registry = MetricRegistry()
    registry.register_measure(Measure(name="revenue", source_field="revenue", aggregation=AggregationType.SUM))
    registry.register_measure(
        Measure(name="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT, colocate_with_field="customer_id")
    )
    registry.register_metric(
        MetricDefinition(name="aov", kind=MetricKind.RATIO, numerator_measure="revenue", denominator_measure="orders")
    )
    return registry


def test_unconfirmed_mappings_are_what_produced_the_wrong_aov():
    registry = _registry()
    raw = bind_metric(registry.resolve("aov"), registry, _real_upload()).measures
    # Documents the bug this view exists to prevent: both wrong mappings sit on order_items.
    assert raw["numerator"].entity == ITEMS and raw["denominator"].entity == ITEMS


def test_the_trusted_view_binds_aov_to_the_right_tables():
    registry = _registry()
    bound = bind_metric(registry.resolve("aov"), registry, _real_upload().trusted()).measures
    assert bound["numerator"].entity == PAYMENTS  # payment_value, not freight_value
    assert bound["denominator"].entity == ORDERS  # the order header, not line items


def test_confirmed_counts_as_trusted_and_rejected_does_not():
    model = _real_upload()
    items = next(e for e in model.entities if e.name == ITEMS)
    items.fields[1].status = "confirmed"  # seller_id -> customer_id
    items.fields[2].status = "rejected"  # freight_value -> revenue
    auto_field = next(f for f in items.fields if f.source_column == "order_id")
    auto_field.status = "rejected"

    trusted_items = next(e for e in model.trusted().entities if e.name == ITEMS)
    assert [f.source_column for f in trusted_items.fields] == ["seller_id"]


def test_an_entity_with_no_trusted_fields_is_dropped_along_with_its_relationships():
    model = _real_upload()
    for field in next(e for e in model.entities if e.name == CUSTOMERS).fields:
        field.status = "rejected"

    trusted = model.trusted()

    assert CUSTOMERS not in {e.name for e in trusted.entities}
    assert all(CUSTOMERS not in (r.from_field + r.to_field) for r in trusted.relationships)
    assert len(trusted.relationships) == 1  # items -> orders survives


def test_trusted_does_not_mutate_the_model_it_was_called_on():
    model = _real_upload()
    model.trusted()
    assert sum(len(e.fields) for e in model.entities) == 8


def test_a_join_through_a_column_whose_mapping_was_rejected_still_passes_the_safety_check(tmp_path):
    """Phase 8 M5: a correct review rejected customers.customer_id as "the customer" (it's per-order;
    the person is customer_unique_id). The orders -> customers foreign key still joins on that
    physical column, and the safety checker refused every "by region" query until relationship join
    columns were allowlisted alongside trusted fields."""
    from insightflow_core.compilation import FieldResolver, SQLCompiler
    from insightflow_core.execution import QueryExecutor
    from insightflow_core.models import AnalyticalQuery, OperationType
    from insightflow_core.pipeline import AnalyticsEnginePipeline
    from insightflow_core.safety import SQLSafetyChecker
    from insightflow_core.validation import ASTValidator

    (tmp_path / f"{ORDERS}.csv").write_text("order_id,customer_id\no1,c1\no2,c2\no3,c1\n")
    (tmp_path / f"{CUSTOMERS}.csv").write_text("customer_id,customer_unique_id,customer_city\nc1,p1,sao paulo\nc2,p2,rio\n")
    model = SemanticModel(
        entities=[
            Entity(name=ORDERS, fields=[_field("order_id", "order_id", ORDERS), _field("customer_id", "customer_id", ORDERS)]),
            Entity(
                name=CUSTOMERS,
                fields=[
                    _field("customer_id", "customer_id", CUSTOMERS, status="rejected"),
                    _field("customer_id", "customer_unique_id", CUSTOMERS, 0.95, "confirmed"),
                    _field("region", "customer_city", CUSTOMERS, 0.85, "confirmed"),
                ],
            ),
        ],
        relationships=[Relationship(from_field=f"{ORDERS}.customer_id", to_field=f"{CUSTOMERS}.customer_id", confidence=1.0, direction="x")],
    ).trusted()
    registry = MetricRegistry()
    registry.register_measure(
        Measure(name="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT, colocate_with_field="customer_id")
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

    result = engine.run(AnalyticalQuery(operation=OperationType.GROUP_BY, metric="orders", dimension="region"))

    assert {row["region"]: row["value"] for row in result.rows} == {"sao paulo": 2, "rio": 1}
