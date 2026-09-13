# Canonical copy for the merged Phase 5 backend -- the one bootstrap_registry() the backend itself
# ever imports. The vendored copies in poc2_analytics_engine/, poc3_dashboard_generation/ and
# poc4_nl_chatbot/ intentionally stay (each POC's standalone tests import its own) and still pin
# `entity="orders"`, which keeps working against their own sample data. This copy no longer does.
"""Registers the target metrics from docs/supported_metrics.md into a fresh MetricRegistry.

Measures name semantic *fields* only, never an entity. Which entity carries `revenue` or `order_id`
is a property of the uploaded dataset, resolved per dataset by insightflow_core's measure binding.
Until Phase 8 M4 every measure here was pinned to `entity="orders"`, which only exists when an
upload's file is literally named orders.csv -- POC 1 names entities after source filenames -- so
every real upload (the Kaggle Olist files first) made query, dashboard and chat fail.

Don't add entity pins back to make one dataset work: a pin that names an entity another upload
lacks makes the measure unresolvable there. Ambiguity is refused per dataset instead (a field on
more than one entity with no sibling measure to co-locate with), which is the honest outcome.
"""
from insightflow_core.models import AggregationType, HavingClause, MetricDefinition, MetricKind
from insightflow_core.models.registry import Measure
from insightflow_core.registry import MetricRegistry


def bootstrap_registry() -> MetricRegistry:
    registry = MetricRegistry()

    # Tier 1: measures (raw aggregatable fields)
    #
    # Key columns sit on both sides of every relationship, so a COUNT DISTINCT of a key is
    # ambiguous on any normalized upload until the measure says which population it counts --
    # by naming another semantic field, never an entity (see Measure.colocate_with_field):
    #   orders    -> order_id where customer_id lives: the order header table, not line items or
    #                payments (Olist carries order_id on orders, order_items, payments and reviews).
    #   customers -> customer_id where order_id lives: customers who ordered, not every customer on
    #                file (customer_id is on both orders and customers, in the sample data too).
    # A hint only narrows a genuinely ambiguous field. On an upload with no orders table at all,
    # `customers` falls back to the only entity carrying customer_id -- all customers on file.
    registry.register_measure(Measure(name="revenue", source_field="revenue", aggregation=AggregationType.SUM))
    registry.register_measure(
        Measure(
            name="orders",
            source_field="order_id",
            aggregation=AggregationType.COUNT_DISTINCT,
            colocate_with_field="customer_id",
        )
    )
    registry.register_measure(
        Measure(
            name="customers",
            source_field="customer_id",
            aggregation=AggregationType.COUNT_DISTINCT,
            colocate_with_field="order_id",
        )
    )
    # Same underlying column as "orders", but registered separately: this one is meant to be
    # aggregated *per customer* (see repeat_purchase_rate's GROUP BY), not across the whole table.
    registry.register_measure(
        Measure(
            name="orders_per_customer",
            source_field="order_id",
            aggregation=AggregationType.COUNT_DISTINCT,
            colocate_with_field="customer_id",
        )
    )

    # Tier 2: named metrics
    # "revenue", "orders", "customers" (plain KPIs) and "Product/Category Performance" and
    # "Regional Performance" (the same measures grouped by a dimension via the AST) need no
    # separate registration -- they're the base measures above, used directly.

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
