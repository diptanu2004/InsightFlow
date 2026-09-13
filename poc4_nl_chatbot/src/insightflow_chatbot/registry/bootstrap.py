# Vendored, unmodified, from poc3_dashboard_generation/src/insightflow/registry/bootstrap.py
# (itself vendored unmodified from poc2_analytics_engine). Same vendoring rationale as POC 3's own
# header comment -- QuestionPlanner and QueryAssembler both need the real, already-tested
# five-metric registry, not a reimplementation. Small, deliberate duplication across POC 2/3/4,
# same reasoning insightflow_core/README.md gives for keeping registry/bootstrap.py out of the
# shared package: this is business configuration (which fields map to which metrics for this
# sample dataset), not shared engine logic.
"""Registers the ten target metrics from docs/supported_metrics.md into a fresh MetricRegistry.

Assumes an `orders` entity with fields `order_id`, `customer_id`, `revenue`, `transaction_date`
(POC 1's real canonical vocabulary name for dates — see `data/semantic_model.json`).
"""
from insightflow_core.models import AggregationType, HavingClause, MetricDefinition, MetricKind
from insightflow_core.models.registry import Measure
from insightflow_core.registry import MetricRegistry


def bootstrap_registry() -> MetricRegistry:
    registry = MetricRegistry()

    # Tier 1: measures (raw aggregatable fields)
    registry.register_measure(Measure(name="revenue", entity="orders", source_field="revenue", aggregation=AggregationType.SUM))
    registry.register_measure(Measure(name="orders", entity="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_measure(Measure(name="customers", entity="orders", source_field="customer_id", aggregation=AggregationType.COUNT_DISTINCT))
    # Same underlying column as "orders", but registered separately: this one is meant to be
    # aggregated *per customer* (see repeat_purchase_rate's GROUP BY), not across the whole table.
    registry.register_measure(
        Measure(name="orders_per_customer", entity="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT)
    )

    # Tier 2: named metrics
    # "revenue", "orders", "customers" (plain KPIs) and "Product/Category Performance" and
    # "Regional Performance" (the same measures grouped by a dimension via the AST) need no
    # separate registration — they're the base measures above, used directly.

    registry.register_metric(
        MetricDefinition(
            name="aov",
            kind=MetricKind.RATIO,
            numerator_measure="revenue",
            denominator_measure="orders",
            description="Average Order Value = revenue / orders",
        )
    )
    registry.register_metric(
        MetricDefinition(
            name="revenue_growth",
            kind=MetricKind.GROWTH,
            base_measure="revenue",
            description="Revenue growth between two periods",
        )
    )
    registry.register_metric(
        MetricDefinition(
            name="order_growth",
            kind=MetricKind.GROWTH,
            base_measure="orders",
            description="Order-count growth between two periods",
        )
    )
    registry.register_metric(
        MetricDefinition(
            name="customer_growth",
            kind=MetricKind.GROWTH,
            base_measure="customers",
            description="Distinct-customer growth between two periods",
        )
    )
    registry.register_metric(
        MetricDefinition(
            name="repeat_purchase_rate",
            kind=MetricKind.HAVING_RATIO,
            numerator_measure="orders_per_customer",
            denominator_measure="customers",
            group_by_field="customer_id",
            having=HavingClause(field="orders_per_customer", operator=">=", value=2),
            description="Share of customers with 2 or more orders",
        )
    )

    return registry
