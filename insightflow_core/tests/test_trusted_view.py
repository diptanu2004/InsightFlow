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
