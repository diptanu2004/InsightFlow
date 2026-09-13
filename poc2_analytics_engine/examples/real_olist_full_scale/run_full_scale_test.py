"""Real-world SCALE integration test: the same real POC 1 output (olist_model.json's semantic
mapping, unchanged) now run against the FULL real Olist dataset (~99.4k orders across 7 CSVs,
downloaded by the user directly from Kaggle) instead of the ~8-16 row referential sample used in
examples/real_olist_integration/.

Reusing the same semantic_model.json is deliberate, not a shortcut: POC 1's mapping is a
column-name -> canonical-field mapping (schema-level), not a row-level one. We verified the full
dataset's CSV headers are byte-identical to the small sample's before writing this script, so the
mapping is still valid; only the row counts changed. That's the whole point of this test — does
POC 2 hold up at real row counts on a mapping it's never seen exercised past a few dozen rows.

bootstrap_real_business_registry() below is copied unchanged from
examples/real_olist_integration/run_real_test.py (same registry judgment calls apply — see that
file's inline comments for the reasoning on each one). Only DATA_DIR and the added timing/
verification code differ.
"""
import json
import time
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
    registry = MetricRegistry()
    registry.register_measure(Measure(name="revenue", entity="payments", source_field="revenue", aggregation=AggregationType.SUM))
    registry.register_measure(Measure(name="orders", entity="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT))
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
            # Fixed after this exact script's full-scale run originally computed exactly 0.0 --
            # see examples/real_olist_integration/run_real_test.py's inline comment and
            # docs/real_world_integration_test.md's "Finding 9" for the full rationale. Groups by
            # the real per-person "customers" entity (join: orders.customer_id ->
            # customers.customer_id) and its customer_unique_id column specifically, bypassing
            # FieldResolver's highest-confidence tie-break (which would otherwise still resolve to
            # the surrogate "customer_id" column even inside the customers entity).
            group_by_entity="customers",
            group_by_source_column="customer_unique_id",
            having=HavingClause(field="orders_per_customer", operator=">=", value=2),
        )
    )
    return registry


def main():
    semantic_model = SemanticModel(**json.loads((DATA_DIR / "semantic_model.json").read_text()))
    registry = bootstrap_real_business_registry()

    t0 = time.perf_counter()
    pipeline = build_pipeline(semantic_model, registry, str(DATA_DIR / "raw"))
    build_ms = (time.perf_counter() - t0) * 1000
    print(f"pipeline build (incl. DuckDB CSV registration for 7 views): {build_ms:.1f}ms\n")

    def run(label, query):
        t = time.perf_counter()
        try:
            result = pipeline.run(query)
            elapsed = (time.perf_counter() - t) * 1000
            payload = result.value if result.value is not None else result.rows
            print(f"{label:22s} -> {payload}   [wall {elapsed:.1f}ms | engine-reported {result.metadata.execution_time_ms:.2f}ms]")
            return result
        except ValueError as e:
            elapsed = (time.perf_counter() - t) * 1000
            print(f"{label:22s} -> REJECTED: {e}   [wall {elapsed:.1f}ms]")
            return None

    print("=== point metrics ===")
    run("revenue", AnalyticalQuery(operation=OperationType.AGGREGATE, metric="revenue"))
    run("orders", AnalyticalQuery(operation=OperationType.AGGREGATE, metric="orders"))
    run("customers", AnalyticalQuery(operation=OperationType.AGGREGATE, metric="customers"))
    run("aov", AnalyticalQuery(operation=OperationType.AGGREGATE, metric="aov"))
    run("repeat_purchase_rate", AnalyticalQuery(operation=OperationType.AGGREGATE, metric="repeat_purchase_rate"))

    print("\n=== growth (2018 vs 2017) ===")
    growth = GrowthSpec(
        current_period=TimeFilter(start_date="2018-01-01", end_date="2018-12-31"),
        comparison_period=TimeFilter(start_date="2017-01-01", end_date="2017-12-31"),
    )
    run("revenue_growth (18v17)", AnalyticalQuery(operation=OperationType.GROWTH, metric="revenue_growth", growth=growth))
    run("order_growth (18v17)", AnalyticalQuery(operation=OperationType.GROWTH, metric="order_growth", growth=growth))
    run("customer_growth (18v17)", AnalyticalQuery(operation=OperationType.GROWTH, metric="customer_growth", growth=growth))

    print("\n=== known-ambiguous real dimensions -- expect clean REJECTED, not a crash ===")
    run("revenue by category", AnalyticalQuery(operation=OperationType.GROUP_BY, metric="revenue", dimension="category", sort=SortSpec(field="category")))
    run("revenue by region", AnalyticalQuery(operation=OperationType.GROUP_BY, metric="revenue", dimension="region", sort=SortSpec(field="region")))


if __name__ == "__main__":
    main()
