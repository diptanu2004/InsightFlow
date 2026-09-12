"""Real-world SCALE integration test for POC 3: the same real Groq-driven `DashboardPlanner` that
`examples/real_olist_integration/` already exercised against an 8-order referential subset, now
run against the FULL real Olist dataset (~99.4k orders across 7 CSVs) that
`poc2_analytics_engine/examples/real_olist_full_scale/` already downloaded and verified. This is
the last item before POC 3 can be considered closed (see top-level README.md's "POC 3 status").

**Deliberate exception to this project's "self-contained POC" rule.** Every other example folder
in POC 3 (`examples/real_olist_integration/`) copies its data byte-for-byte into its own
directory rather than reading another POC's files at runtime. This script does NOT do that: the
full dataset is ~50MB across 7 CSVs, and the device bridge used to move files into/out of this
project's cloud sandbox has already failed repeatedly on anything over ~2MB (documented in
`poc2_analytics_engine/docs/real_world_integration_test.md`'s "Part 2" section) -- copying it a
second time into this POC would hit the same wall for no benefit, since this script (like every
real-Groq-call script in this project) can only actually be RUN on the user's own machine anyway
(no `GROQ_API_KEY` in the cloud sandbox). `DATA_DIR` below points at
`poc2_analytics_engine/examples/real_olist_full_scale/data/` directly -- a sibling POC folder on
disk, not a package import (still no cross-venv dependency) -- by explicit user decision, not an
oversight. If that assumption (both POC folders as siblings under the same parent directory) ever
stops holding, pass `--data-dir` to point at wherever the full CSVs actually live.

Registry and dataset provenance are otherwise identical to `examples/real_olist_integration/` and
`poc2_analytics_engine/examples/real_olist_full_scale/` -- see those files' own docstrings/README
for the full judgment-call rationale (which "revenue" column to trust, the
customer_id/customer_unique_id surrogate-key gap, etc.). Nothing about the mapping changes between
the small subset and the full dataset -- only row counts -- which is the entire point of this
script: does everything downstream of a real Groq call (`DashboardValidator`, the vendored
`AnalyticsEnginePipeline`, `DashboardDataResolver`) hold up at ~99.4k orders instead of 8, on a
mapping and a compiler (including the join-fan-out fix -- see
`docs/real_world_integration_test.md`'s "Finding 4") that had only ever been run against a
handful of rows.
"""
import argparse
import json
import time
from datetime import date
from pathlib import Path

from insightflow_core.compilation import FieldResolver, SQLCompiler
from insightflow.dashboard.pipeline import DashboardGenerationPipeline
from insightflow.dashboard.planner import DashboardPlanner
from insightflow.dashboard.resolver import DashboardDataResolver
from insightflow.dashboard.signal import SignalGatherer
from insightflow.dashboard.spec import GROUPING_COMPONENT_TYPES
from insightflow.dashboard.validator import DashboardValidator
from insightflow_core.execution import QueryExecutor
from insightflow.llm.groq_client import GroqLLMClient
from insightflow_core.models import AggregationType, HavingClause, MetricDefinition, MetricKind, SemanticModel
from insightflow_core.models.registry import Measure
from insightflow_core.pipeline import AnalyticsEnginePipeline
from insightflow.registry import MetricRegistry
from insightflow_core.safety import SQLSafetyChecker
from insightflow_core.validation import ASTValidator

# Sibling POC folder on the user's machine -- see module docstring for why this isn't a copy.
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "poc2_analytics_engine" / "examples" / "real_olist_full_scale" / "data"


def bootstrap_real_business_registry() -> MetricRegistry:
    """Copied unchanged from examples/real_olist_integration/run_real_test.py -- see that file's
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
            # Fixed after this exact script's full-scale run originally computed exactly 0.0 --
            # see examples/real_olist_integration/run_real_test.py's inline comment and
            # docs/real_world_integration_test.md's "Finding 5" for the full rationale. Groups by
            # the real per-person "customers" entity (join: orders.customer_id ->
            # customers.customer_id) and its customer_unique_id column specifically, bypassing
            # FieldResolver's highest-confidence tie-break (which would otherwise still resolve to
            # the surrogate "customer_id" column even inside the customers entity).
            group_by_entity="customers",
            group_by_source_column="customer_unique_id",
            having=HavingClause(field="orders_per_customer", operator=">=", value=2),
            description="Share of customers with 2 or more orders",
        )
    )
    return registry


def _discover_reference_date(semantic_model: SemanticModel, field_resolver: FieldResolver, data_dir: Path) -> date:
    """SignalGatherer needs an explicit `reference_date` to build its growth-comparison windows
    (see examples/real_olist_integration/run_real_test.py's own REFERENCE_DATE comment -- POC 2
    has no `MAX(date)` capability of its own, a documented gap, not something this script works
    around inside the engine). Rather than hardcode a date guessed for a specific CSV snapshot
    (fragile -- the exact full-scale download's max date isn't verified from inside this sandbox,
    which never received the full CSVs), read it directly off whichever physical column
    `transaction_date` actually resolves to for `orders`, via a disposable DuckDB query -- the
    same resolution FieldResolver/SQLCompiler would do, just run once up front instead of assumed.
    """
    import duckdb

    loc = field_resolver.resolve_field("orders", "transaction_date")
    csv_path = data_dir / f"{loc.source_file}.csv"
    con = duckdb.connect(":memory:")
    try:
        max_ts = con.execute(f"SELECT MAX(\"{loc.source_column}\") FROM read_csv_auto('{csv_path}')").fetchone()[0]
    finally:
        con.close()
    if max_ts is None:
        raise ValueError(f'"{loc.source_column}" in {csv_path} has no non-null values -- cannot derive a reference_date')
    return max_ts.date() if hasattr(max_ts, "date") else max_ts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory holding semantic_model.json + raw/*.csv (default: the full-scale dataset "
        "already downloaded for poc2_analytics_engine's own full-scale test).",
    )
    args = parser.parse_args()
    data_dir = args.data_dir

    from insightflow.config import settings

    semantic_model = SemanticModel(**json.loads((data_dir / "semantic_model.json").read_text()))
    registry = bootstrap_real_business_registry()
    field_resolver = FieldResolver(semantic_model)

    reference_date = _discover_reference_date(semantic_model, field_resolver, data_dir / "raw")
    print(f"reference_date (max orders.transaction_date in this dataset): {reference_date}")

    t0 = time.perf_counter()
    executor = QueryExecutor(settings.duckdb_path, settings.query_timeout_seconds, settings.max_row_limit)
    executor.register_sources(semantic_model, str(data_dir / "raw"))
    engine = AnalyticsEnginePipeline(
        registry=registry,
        validator=ASTValidator(registry, semantic_model, settings.max_row_limit),
        compiler=SQLCompiler(registry, field_resolver, settings.max_row_limit),
        checker=SQLSafetyChecker(semantic_model, settings.max_row_limit),
        executor=executor,
    )
    build_ms = (time.perf_counter() - t0) * 1000
    print(f"pipeline build (incl. DuckDB CSV registration for 7 views): {build_ms:.1f}ms")

    pipeline = DashboardGenerationPipeline(
        semantic_model=semantic_model,
        registry=registry,
        signal_gatherer=SignalGatherer(engine, semantic_model, registry, reference_date=reference_date),
        planner=DashboardPlanner(GroqLLMClient(), settings.dashboard_min_components, settings.dashboard_max_components),
        validator=DashboardValidator(registry, field_resolver, settings.dashboard_min_components, settings.dashboard_max_components),
        resolver=DashboardDataResolver(engine),
        field_resolver=field_resolver,
    )

    t1 = time.perf_counter()
    dashboard = pipeline.run()
    run_ms = (time.perf_counter() - t1) * 1000
    print(f"dashboard generation (signals + real Groq call + validation + resolution): {run_ms:.1f}ms\n")

    print(dashboard.model_dump_json(indent=2))

    # Mechanical check from hld.md's evaluation step ("absence of unsupported metrics") -- this
    # is already enforced by DashboardValidator before pipeline.run() can return at all, so a
    # HydratedDashboard reaching this point already proves it; printed here for visibility rather
    # than as a separate check.
    print(f"\n{len(dashboard.components)} components, all validator-approved:")
    for c in dashboard.components:
        shape = "grouped" if c.spec.type in GROUPING_COMPONENT_TYPES else "scalar"
        print(f"  - {c.spec.component_id} ({c.spec.type.value}, {shape}): metric={c.spec.metric_name!r} dimension={c.spec.dimension!r}")


if __name__ == "__main__":
    main()
