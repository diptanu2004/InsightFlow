"""Regression tests for the two synthetic benchmark datasets built for POC 3's evaluation step
(docs/hld.md's open "benchmark semantic models" item -- a declining-revenue case and a
retention-problem case, alongside the one "healthy" case data/ and examples/real_olist_integration/
already cover). See examples/benchmark_declining_revenue/generate_data.py and
examples/benchmark_retention_problem/generate_data.py's module docstrings for the deliberate shape
of each dataset and why.

These tests do NOT call a real LLM (no GROQ_API_KEY in the cloud sandbox this POC was built in,
same limitation as every other real-Groq-run test in this project) -- they verify the two things
that don't need one: (1) the real engine, run against each dataset, reproduces the exact numbers
independently computed in plain Python at data-generation time (data/expected_values.json), so
when the user runs the actual Groq-backed benchmark script (examples/*/run_benchmark_test.py) on
their own machine, any surprising planner output can be checked against ground truth that was
verified BEFORE a real LLM call, not after; and (2) SignalGatherer surfaces the intended signal
shape for each case (a real decline for one, a real retention problem hidden under healthy growth
for the other) mechanically, before any human/LLM judgment is needed.
"""
import json
from pathlib import Path

import pytest

from insightflow_core.compilation import FieldResolver, SQLCompiler
from insightflow_dashboard.dashboard.signal import SignalGatherer
from insightflow_core.execution import QueryExecutor
from insightflow_core.models import SemanticModel
from insightflow_core.pipeline import AnalyticsEnginePipeline
from insightflow_dashboard.registry import bootstrap_registry
from insightflow_core.safety import SQLSafetyChecker
from insightflow_core.validation import ASTValidator

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

CASES = ["benchmark_declining_revenue", "benchmark_retention_problem"]


def _build_engine(data_dir: Path) -> AnalyticsEnginePipeline:
    semantic_model = SemanticModel(**json.loads((data_dir / "semantic_model.json").read_text()))
    registry = bootstrap_registry()
    field_resolver = FieldResolver(semantic_model)
    executor = QueryExecutor(":memory:", 10, 10_000)
    executor.register_sources(semantic_model, str(data_dir / "raw"))
    return AnalyticsEnginePipeline(
        registry=registry,
        validator=ASTValidator(registry, semantic_model, 10_000),
        compiler=SQLCompiler(registry, field_resolver, 10_000),
        checker=SQLSafetyChecker(semantic_model, 10_000),
        executor=executor,
    )


@pytest.fixture(params=CASES)
def benchmark_case(request):
    data_dir = EXAMPLES_DIR / request.param / "data"
    expected = json.loads((data_dir / "expected_values.json").read_text())
    engine = _build_engine(data_dir)
    semantic_model = SemanticModel(**json.loads((data_dir / "semantic_model.json").read_text()))
    registry = bootstrap_registry()
    return {"name": request.param, "engine": engine, "expected": expected, "semantic_model": semantic_model, "registry": registry}


def test_engine_totals_match_independently_computed_expected_values(benchmark_case):
    """Confirms the CSVs generate_data.py wrote and the numbers it computed independently (in
    plain Python, no engine involved) agree with what the real engine reports -- this is checking
    the DATASET and the ENGINE against each other, not re-testing SQLCompiler itself (that's
    tests/test_join_fanout_fix.py and the rest of the suite)."""
    from insightflow_core.models import AnalyticalQuery, OperationType

    engine, expected = benchmark_case["engine"], benchmark_case["expected"]
    for metric, key in [("revenue", "total_revenue"), ("orders", "total_orders"), ("customers", "total_customers")]:
        result = engine.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric=metric))
        assert result.value == pytest.approx(expected[key]), f"{benchmark_case['name']}: {metric} mismatch"

    repeat = engine.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="repeat_purchase_rate"))
    assert repeat.value == pytest.approx(expected["repeat_purchase_rate"], abs=1e-3), f"{benchmark_case['name']}: repeat_purchase_rate mismatch"


def test_engine_growth_signals_match_independently_computed_expected_values(benchmark_case):
    from datetime import date, timedelta

    from insightflow_core.models import AnalyticalQuery, GrowthSpec, OperationType, TimeFilter

    engine, expected = benchmark_case["engine"], benchmark_case["expected"]
    reference_date = date.fromisoformat(expected["reference_date"])
    current = TimeFilter(start_date=reference_date - timedelta(days=89), end_date=reference_date)
    comparison_end = current.start_date - timedelta(days=1)
    comparison = TimeFilter(start_date=comparison_end - timedelta(days=89), end_date=comparison_end)
    growth = GrowthSpec(current_period=current, comparison_period=comparison)

    for metric, key in [("revenue_growth", "revenue_growth"), ("order_growth", "order_growth"), ("customer_growth", "customer_growth")]:
        result = engine.run(AnalyticalQuery(operation=OperationType.GROWTH, metric=metric, growth=growth))
        assert result.value == pytest.approx(expected[key], abs=1e-6), f"{benchmark_case['name']}: {metric} mismatch"


def test_signal_gatherer_surfaces_the_intended_shape_for_each_case():
    """Mechanical check of hld.md's "revenue declining -> emphasize revenue trend" intent, one
    layer below the planner: does SignalGatherer's OWN pre-planning signal set already show the
    pattern each dataset was built to have, before any LLM judgment is involved at all?"""
    from datetime import date

    declining_dir = EXAMPLES_DIR / "benchmark_declining_revenue" / "data"
    declining_expected = json.loads((declining_dir / "expected_values.json").read_text())
    declining_engine = _build_engine(declining_dir)
    declining_semantic_model = SemanticModel(**json.loads((declining_dir / "semantic_model.json").read_text()))
    declining_registry = bootstrap_registry()
    declining_signals = {
        s.name: s.result.value
        for s in SignalGatherer(
            declining_engine, declining_semantic_model, declining_registry, reference_date=date.fromisoformat(declining_expected["reference_date"])
        ).gather()
    }
    assert declining_signals["revenue_growth"] < -0.1, "declining-revenue case should show a clearly negative revenue_growth signal"
    assert declining_signals["order_growth"] < -0.1
    assert declining_signals["customer_growth"] < -0.1

    retention_dir = EXAMPLES_DIR / "benchmark_retention_problem" / "data"
    retention_expected = json.loads((retention_dir / "expected_values.json").read_text())
    retention_engine = _build_engine(retention_dir)
    retention_semantic_model = SemanticModel(**json.loads((retention_dir / "semantic_model.json").read_text()))
    retention_registry = bootstrap_registry()
    retention_signals = {
        s.name: s.result.value
        for s in SignalGatherer(
            retention_engine, retention_semantic_model, retention_registry, reference_date=date.fromisoformat(retention_expected["reference_date"])
        ).gather()
    }
    # The whole point of this case: top-line growth signals look healthy...
    assert retention_signals["revenue_growth"] > 0.1, "retention-problem case's revenue_growth should look healthy on the surface"
    assert retention_signals["order_growth"] > 0.1
    assert retention_signals["customer_growth"] > 0.1
    # ...but repeat_purchase_rate (not one of SignalGatherer's fixed signals -- it takes no time
    # filter and isn't in _BASE_KPI_SIGNALS/_GROWTH_SIGNALS) tells the real story when queried
    # directly, which is exactly what the planner is expected to do with it per PlannerContext's
    # resolvable_metrics/groupable_metrics -- it's offered, just not pre-fetched as a signal.
    from insightflow_core.models import AnalyticalQuery, OperationType

    repeat = retention_engine.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="repeat_purchase_rate"))
    assert repeat.value < 0.15, "retention-problem case should have a clearly low repeat_purchase_rate despite healthy top-line growth"
