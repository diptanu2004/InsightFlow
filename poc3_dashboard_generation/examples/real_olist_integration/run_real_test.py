"""Real-world integration test for POC 3: real Olist data, a hand-configured registry matching
POC 1's real (imperfect) field mapping, and a REAL Groq call through DashboardPlanner -- the one
piece of POC 3 that had never been exercised against a live LLM in any environment before this
(see the top-level README's POC 3 status line and this POC's own README's
"Running against a real LLM").

Everything under `data/` is copied byte-for-byte from
`poc2_analytics_engine/examples/real_olist_integration/data/` (itself copied byte-for-byte from
POC 1's actual olist_model.json output plus the real, unmodified Olist CSVs -- see that folder's
own README for full dataset provenance and the five real POC1->POC2 gaps it originally surfaced).
Copied rather than referenced across POC folders, per top-level README.md's "self-contained POC"
rule -- this POC does not read another POC's files at runtime.

The registry below is copied UNCHANGED from that same POC 2 example's
`bootstrap_real_business_registry()`. Every judgment call documented there still applies
identically here, since this is the same POC 1 output feeding the same vendored engine:
  - "revenue" is mapped by POC 1 to BOTH order_items.freight_value (confidence 0.55, actually
    shipping cost) and payments.payment_value (confidence 0.95, the real amount paid). We pick
    payments.payment_value.
  - "customers" (COUNT_DISTINCT customer_id on `orders`) counts POC 1's per-order surrogate ID,
    not Olist's true stable `customer_unique_id` -- POC 1 collapsed both onto one canonical name,
    so POC 2/3 cannot address the true identity separately through the plain "customer_id" name.
    Still true for every metric built directly on the "customers"/"orders" measures above (e.g.
    customer_growth): a real, not-fixed-here vocabulary gap.
  - "repeat_purchase_rate" IS fixed below, via group_by_entity/group_by_source_column: found via
    the full-scale real-Olist run computing exactly 0.0 (every order's surrogate customer_id is
    unique to that order, so "≥2 orders per group" was structurally always false) -- see
    docs/real_world_integration_test.md's "Finding 5" and MetricDefinition's own docstring.

POC 3's own generic `registry.bootstrap.bootstrap_registry()` CANNOT be reused against this
dataset: it hardcodes `entity="orders"` for revenue/customers, which is POC 3's own hand-built
sample dataset's shape, not this real one.

This script also can't use `dashboard.pipeline.build_dashboard_pipeline()`'s convenience wiring
unchanged, because `SignalGatherer`'s growth-window calculation needs an explicit
`reference_date` here (see REFERENCE_DATE below and this folder's README) -- so the pipeline is
wired by hand instead, the same five stages `build_dashboard_pipeline` would otherwise construct.
"""
import json
from datetime import date
from pathlib import Path

from insightflow_core.compilation import FieldResolver, SQLCompiler
from insightflow.dashboard.pipeline import DashboardGenerationPipeline
from insightflow.dashboard.planner import DashboardPlanner
from insightflow.dashboard.resolver import DashboardDataResolver
from insightflow.dashboard.signal import SignalGatherer
from insightflow.dashboard.validator import DashboardValidator
from insightflow_core.execution import QueryExecutor
from insightflow.llm.groq_client import GroqLLMClient
from insightflow_core.models import AggregationType, HavingClause, MetricDefinition, MetricKind, SemanticModel
from insightflow_core.models.registry import Measure
from insightflow_core.pipeline import AnalyticsEnginePipeline
from insightflow.registry import MetricRegistry
from insightflow_core.safety import SQLSafetyChecker
from insightflow_core.validation import ASTValidator

DATA_DIR = Path(__file__).resolve().parent / "data"

# The small real subset's own latest order date (see orders.csv). SignalGatherer's growth windows
# are computed as [reference_date-89d, reference_date] vs. the preceding 90 days; a wall-clock
# `date.today()` default would put both windows entirely outside this historically-dated dataset
# (README's "Found while implementing SignalGatherer" finding) -- 8 orders over ~16 months means
# even this explicit reference_date will likely leave both growth windows sparse or empty, which
# SignalGatherer is designed to tolerate (see below), not something to fix by picking a "better"
# date.
REFERENCE_DATE = date(2018, 8, 3)


def bootstrap_real_business_registry() -> MetricRegistry:
    """Copied unchanged from
    poc2_analytics_engine/examples/real_olist_integration/run_real_test.py -- see that file's
    inline comments for the full rationale behind every registration below."""
    registry = MetricRegistry()

    registry.register_measure(Measure(name="revenue", entity="payments", source_field="revenue", aggregation=AggregationType.SUM))
    registry.register_measure(Measure(name="orders", entity="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_measure(Measure(name="customers", entity="orders", source_field="customer_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_measure(
        Measure(name="orders_per_customer", entity="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT)
    )

    registry.register_metric(
        MetricDefinition(name="aov", kind=MetricKind.RATIO, numerator_measure="revenue", denominator_measure="orders", description="Average Order Value")
    )
    registry.register_metric(MetricDefinition(name="revenue_growth", kind=MetricKind.GROWTH, base_measure="revenue", description="Revenue growth between two periods"))
    registry.register_metric(MetricDefinition(name="order_growth", kind=MetricKind.GROWTH, base_measure="orders", description="Order-count growth between two periods"))
    registry.register_metric(MetricDefinition(name="customer_growth", kind=MetricKind.GROWTH, base_measure="customers", description="Distinct-customer growth between two periods"))
    registry.register_metric(
        MetricDefinition(
            name="repeat_purchase_rate",
            kind=MetricKind.HAVING_RATIO,
            numerator_measure="orders_per_customer",
            denominator_measure="customers",
            group_by_field="customer_id",
            # Fixed after the full-scale run: group by the real per-person "customers" entity via
            # the join (orders.customer_id -> customers.customer_id), and the specific
            # customer_unique_id physical column within it (bypassing FieldResolver's
            # highest-confidence tie-break, which would otherwise still pick the surrogate
            # "customer_id" column even inside the customers entity -- both map to the same
            # canonical name there too). Without these two overrides this metric silently
            # computes 0.0 against real Olist data -- see docs/real_world_integration_test.md's
            # "Finding 5".
            group_by_entity="customers",
            group_by_source_column="customer_unique_id",
            having=HavingClause(field="orders_per_customer", operator=">=", value=2),
            description="Share of customers with 2 or more orders",
        )
    )
    return registry


def main():
    from insightflow.config import settings

    semantic_model = SemanticModel(**json.loads((DATA_DIR / "semantic_model.json").read_text()))
    registry = bootstrap_real_business_registry()

    field_resolver = FieldResolver(semantic_model)
    executor = QueryExecutor(settings.duckdb_path, settings.query_timeout_seconds, settings.max_row_limit)
    executor.register_sources(semantic_model, str(DATA_DIR / "raw"))
    engine = AnalyticsEnginePipeline(
        registry=registry,
        validator=ASTValidator(registry, semantic_model, settings.max_row_limit),
        compiler=SQLCompiler(registry, field_resolver, settings.max_row_limit),
        checker=SQLSafetyChecker(semantic_model, settings.max_row_limit),
        executor=executor,
    )

    pipeline = DashboardGenerationPipeline(
        semantic_model=semantic_model,
        registry=registry,
        signal_gatherer=SignalGatherer(engine, semantic_model, registry, reference_date=REFERENCE_DATE),
        planner=DashboardPlanner(GroqLLMClient(), settings.dashboard_min_components, settings.dashboard_max_components),
        validator=DashboardValidator(registry, field_resolver, settings.dashboard_min_components, settings.dashboard_max_components),
        resolver=DashboardDataResolver(engine),
        field_resolver=field_resolver,
    )

    dashboard = pipeline.run()
    print(dashboard.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
