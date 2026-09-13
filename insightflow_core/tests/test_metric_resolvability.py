"""Registered is not the same as runnable on a given dataset -- see metric_resolvability.py.

The "real Olist upload" model below is a trimmed copy of what POC 1 actually produced when the Kaggle
Olist files went through the Phase 8 upload UI: entity names are the source filename stems, so a
registry written against the sample dataset's `orders` entity resolves nothing.
"""
from insightflow_core.models import (
    AggregationType,
    AnalyticalQuery,
    Entity,
    HavingClause,
    MetricDefinition,
    MetricKind,
    OperationType,
    Relationship,
    SemanticField,
    SemanticModel,
)
from insightflow_core.models.registry import Measure
from insightflow_core.registry import MetricRegistry
from insightflow_core.validation import ASTValidator
from insightflow_core.validation.metric_resolvability import resolvable_metric_names, unresolvable_reason


def _field(name, column, file, confidence=0.99):
    return SemanticField(name=name, source_column=column, source_file=file, confidence=confidence)


def _sample_shaped_model() -> SemanticModel:
    return SemanticModel(
        entities=[
            Entity(
                name="orders",
                fields=[
                    _field("order_id", "order_id", "orders"),
                    _field("customer_id", "customer_id", "orders"),
                    _field("revenue", "revenue", "orders"),
                    _field("transaction_date", "order_date", "orders"),
                ],
            ),
            Entity(name="customers", fields=[_field("customer_id", "customer_id", "customers")]),
        ],
        relationships=[
            Relationship(
                from_field="orders.customer_id", to_field="customers.customer_id", confidence=0.99, direction="x"
            )
        ],
    )


def _real_olist_upload_model() -> SemanticModel:
    return SemanticModel(
        entities=[
            Entity(
                name="olist_order_payments_dataset",
                fields=[
                    _field("order_id", "order_id", "olist_order_payments_dataset"),
                    _field("revenue", "payment_value", "olist_order_payments_dataset", 0.98),
                ],
            ),
            Entity(
                name="olist_customers_dataset",
                fields=[_field("customer_id", "customer_id", "olist_customers_dataset")],
            ),
        ],
        relationships=[],
    )


def _registry() -> MetricRegistry:
    """The same shape as every registry/bootstrap.py copy: all measures pinned to `orders`."""
    registry = MetricRegistry()
    registry.register_measure(Measure(name="revenue", entity="orders", source_field="revenue", aggregation=AggregationType.SUM))
    registry.register_measure(Measure(name="orders", entity="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_measure(
        Measure(name="customers", entity="orders", source_field="customer_id", aggregation=AggregationType.COUNT_DISTINCT)
    )
    registry.register_metric(
        MetricDefinition(name="aov", kind=MetricKind.RATIO, numerator_measure="revenue", denominator_measure="orders")
    )
    registry.register_metric(MetricDefinition(name="revenue_growth", kind=MetricKind.GROWTH, base_measure="revenue"))
    registry.register_metric(
        MetricDefinition(
            name="repeat_purchase_rate",
            kind=MetricKind.HAVING_RATIO,
            numerator_measure="orders",
            denominator_measure="customers",
            group_by_field="customer_id",
            having=HavingClause(field="orders", operator=">=", value=2),
        )
    )
    return registry


def test_every_metric_resolves_against_a_sample_shaped_dataset():
    registry = _registry()
    assert resolvable_metric_names(registry, _sample_shaped_model()) == {
        "revenue",
        "orders",
        "customers",
        "aov",
        "revenue_growth",
        "repeat_purchase_rate",
    }


def test_nothing_resolves_against_filename_named_entities():
    registry = _registry()
    model = _real_olist_upload_model()

    assert resolvable_metric_names(registry, model) == set()
    reason = unresolvable_reason("revenue", registry, model)
    assert reason is not None and '"orders"' in reason and "does not have" in reason
    # A composite metric reports the measure that actually broke, not a generic failure.
    assert '"revenue"' in unresolvable_reason("aov", registry, model)


def test_growth_needs_a_time_field_on_its_measures_entity():
    registry = _registry()
    model = _sample_shaped_model()
    model.entities[0].fields = [f for f in model.entities[0].fields if f.name != "transaction_date"]

    reason = unresolvable_reason("revenue_growth", registry, model)
    assert reason is not None and "transaction_date" in reason
    # Only growth depends on the time field -- the plain measure still resolves.
    assert unresolvable_reason("revenue", registry, model) is None


def test_having_ratio_group_entity_must_be_reachable_by_a_relationship():
    registry = MetricRegistry()
    registry.register_measure(Measure(name="orders", entity="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_measure(
        Measure(name="customers", entity="orders", source_field="customer_id", aggregation=AggregationType.COUNT_DISTINCT)
    )
    registry.register_metric(
        MetricDefinition(
            name="repeat_purchase_rate",
            kind=MetricKind.HAVING_RATIO,
            numerator_measure="orders",
            denominator_measure="customers",
            group_by_field="customer_id",
            group_by_entity="customers",
            having=HavingClause(field="orders", operator=">=", value=2),
        )
    )
    model = _sample_shaped_model()
    assert unresolvable_reason("repeat_purchase_rate", registry, model) is None

    model.relationships = []
    reason = unresolvable_reason("repeat_purchase_rate", registry, model)
    assert reason is not None and "relationship" in reason


def test_ast_validator_rejects_an_unresolvable_metric_instead_of_crashing_in_the_compiler():
    registry = _registry()
    validator = ASTValidator(registry, _real_olist_upload_model(), max_row_limit=1000)

    result = validator.validate(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="revenue"))

    assert not result.is_valid
    assert [e.code for e in result.errors] == ["unresolvable_metric"]
