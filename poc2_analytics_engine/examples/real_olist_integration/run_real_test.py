"""Full application-level integration test: real POC 1 output (olist_model.json, produced by an
earlier real run of POC 1's LLM-backed pipeline against the real, unmodified Olist e-commerce
CSVs) feeding directly into a fresh POC 2 run — no synthetic data on either side of the
POC1 -> POC2 boundary.
"""
import json
from pathlib import Path

from insightflow_core.models import (
    AggregationType,
    AnalyticalQuery,
    GrowthSpec,
    HavingClause,
    MetricDefinition,
    MetricKind,
    OperationType,
    SemanticModel,
    SortSpec,
    TimeFilter,
)
from insightflow_core.models.registry import Measure
from insightflow_core.pipeline import build_pipeline
from insightflow_analytics.registry import MetricRegistry

DATA_DIR = Path(__file__).resolve().parent / "data"


def bootstrap_real_business_registry() -> MetricRegistry:
    """Registered by hand against POC 1's REAL olist_model.json output — not the sample data's
    bootstrap_registry(). Every choice below is a judgment call an analyst configuring POC 2
    against a real (imperfect) POC 1 mapping would have to make; each is explained inline.
    """
    registry = MetricRegistry()

    # "revenue" is mapped to TWO different physical columns by POC 1, in two different entities:
    #   order_items.freight_value (confidence 0.55 -- POC1's own README already flags this as
    #     likely mislabeled; it's shipping cost, not order revenue)
    #   payments.payment_value    (confidence 0.95 -- the actual amount the customer paid)
    # We deliberately pick payments.payment_value: higher confidence AND the semantically
    # correct choice. FieldResolver can't express "the 0.55 one, not the 0.95 one" within a
    # single entity's ambiguity resolution (that's resolve_field's job, and there's no collision
    # *within* order_items or *within* payments here) -- this is a cross-entity choice, made in
    # registry configuration, same as choosing which entity to point a Measure at in the sample
    # bootstrap_registry().
    registry.register_measure(Measure(name="revenue", entity="payments", source_field="revenue", aggregation=AggregationType.SUM))

    registry.register_measure(Measure(name="orders", entity="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT))

    # CAVEAT (real finding, not fixed here): canonical "customer_id" is POC 1's mapping for BOTH
    # orders.customer_id (a per-order surrogate in the real Olist schema) and, within the
    # `customers` entity, customer_id/customer_unique_id (customer_unique_id is the true stable
    # customer identity -- Olist deliberately gives repeat customers a new customer_id per
    # order). Because POC1 collapsed the surrogate and the true identity onto the SAME canonical
    # name, POC2 cannot address "the stable identity one" through the canonical layer at all --
    # there is no canonical name left to ask for it by. This measure will systematically COUNT
    # PER-ORDER SURROGATES, not true unique humans. This is a POC1 vocabulary gap (documented in
    # POC1's own README under "vocabulary is too coarse"), not something POC2 can route around --
    # still true for every metric built directly on this measure (e.g. customer_growth below).
    # FIXED for repeat_purchase_rate specifically, though: see its group_by_entity/
    # group_by_source_column below and docs/real_world_integration_test.md's "Finding 9" (the
    # full-scale run found this measure's surrogate counting make repeat_purchase_rate compute
    # exactly 0.0 -- every order's own surrogate customer_id is unique to that order, so grouping
    # by it can never see a repeat purchase at all).
    registry.register_measure(Measure(name="customers", entity="orders", source_field="customer_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_measure(Measure(name="orders_per_customer", entity="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT))

    registry.register_metric(MetricDefinition(name="aov", kind=MetricKind.RATIO, numerator_measure="revenue", denominator_measure="orders", description="Average Order Value"))
    registry.register_metric(MetricDefinition(name="revenue_growth", kind=MetricKind.GROWTH, base_measure="revenue"))
    registry.register_metric(MetricDefinition(name="order_growth", kind=MetricKind.GROWTH, base_measure="orders"))
    registry.register_metric(MetricDefinition(name="customer_growth", kind=MetricKind.GROWTH, base_measure="customers"))
    registry.register_metric(
        MetricDefinition(
            name="repeat_purchase_rate",
            kind=MetricKind.HAVING_RATIO,
            numerator_measure="orders_per_customer",
            denominator_measure="customers",
            group_by_field="customer_id",
            # Fixed after the full-scale run -- see the "customers" measure's comment above and
            # docs/real_world_integration_test.md's "Finding 9". Groups by the real per-person
            # "customers" entity (join: orders.customer_id -> customers.customer_id) and its
            # customer_unique_id column specifically, bypassing FieldResolver's
            # highest-confidence tie-break (which would otherwise still resolve to the surrogate
            # "customer_id" column even inside the customers entity).
            group_by_entity="customers",
            group_by_source_column="customer_unique_id",
            having=HavingClause(field="orders_per_customer", operator=">=", value=2),
        )
    )
    return registry


def main():
    semantic_model = SemanticModel(**json.loads((DATA_DIR / "semantic_model.json").read_text()))
    registry = bootstrap_real_business_registry()
    pipeline = build_pipeline(semantic_model, registry, str(DATA_DIR / "raw"))

    def run(label, query):
        try:
            result = pipeline.run(query)
            payload = result.value if result.value is not None else result.rows
            print(f"{label:22s} -> {payload}   [{result.metadata.execution_time_ms:.2f}ms | sql: {result.metadata.sql}]")
        except ValueError as e:
            print(f"{label:22s} -> REJECTED: {e}")

    run("revenue", AnalyticalQuery(operation=OperationType.AGGREGATE, metric="revenue"))
    run("orders", AnalyticalQuery(operation=OperationType.AGGREGATE, metric="orders"))
    run("customers", AnalyticalQuery(operation=OperationType.AGGREGATE, metric="customers"))
    run("aov", AnalyticalQuery(operation=OperationType.AGGREGATE, metric="aov"))
    run("repeat_purchase_rate", AnalyticalQuery(operation=OperationType.AGGREGATE, metric="repeat_purchase_rate"))

    growth = GrowthSpec(
        current_period=TimeFilter(start_date="2018-01-01", end_date="2018-12-31"),
        comparison_period=TimeFilter(start_date="2017-01-01", end_date="2017-12-31"),
    )
    run("revenue_growth (18v17)", AnalyticalQuery(operation=OperationType.GROWTH, metric="revenue_growth", growth=growth))
    run("order_growth (18v17)", AnalyticalQuery(operation=OperationType.GROWTH, metric="order_growth", growth=growth))
    run("customer_growth (18v17)", AnalyticalQuery(operation=OperationType.GROWTH, metric="customer_growth", growth=growth))

    # Known-ambiguous real dimensions -- expected to be cleanly REJECTED, not crash.
    run("revenue by category", AnalyticalQuery(operation=OperationType.GROUP_BY, metric="revenue", dimension="category", sort=SortSpec(field="category")))
    run("revenue by region", AnalyticalQuery(operation=OperationType.GROUP_BY, metric="revenue", dimension="region", sort=SortSpec(field="region")))


if __name__ == "__main__":
    main()
