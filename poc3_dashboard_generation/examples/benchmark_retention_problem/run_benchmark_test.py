"""Runs POC 3's full DashboardGenerationPipeline -- including a REAL Groq call -- against the
synthetic retention-problem benchmark dataset (see generate_data.py's module docstring for the
dataset's deliberate shape: revenue/order/customer growth all clearly POSITIVE -- the business
looks healthy on every top-line signal -- while repeat_purchase_rate is far below the healthy
baseline, because almost all of that growth is new-customer acquisition covering for churn).
Same wiring pattern as examples/real_olist_integration/run_real_test.py, minus the real-data
judgment calls that script needs (this dataset's registry is just `bootstrap_registry()`,
unchanged, since it uses the same entity shape as this POC's own `data/`).

Like every other real-Groq-call script in this project, this can only be run on a machine with a
real `GROQ_API_KEY` (none exists in the cloud sandbox this POC was built in) -- see this folder's
README.md for how to run it and what to look for in the output (the evaluation rubric).
`data/expected_values.json` (written by generate_data.py, verified independently against the real
engine in tests/test_benchmark_datasets.py) is ground truth for every number that should appear in
the KPI/growth components -- any dashboard number that disagrees with it is a real bug, not a
planner judgment call.
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
from insightflow_core.models import SemanticModel
from insightflow_core.pipeline import AnalyticsEnginePipeline
from insightflow.registry import bootstrap_registry
from insightflow_core.safety import SQLSafetyChecker
from insightflow_core.validation import ASTValidator

DATA_DIR = Path(__file__).resolve().parent / "data"


def main():
    from insightflow.config import settings

    semantic_model = SemanticModel(**json.loads((DATA_DIR / "semantic_model.json").read_text()))
    expected = json.loads((DATA_DIR / "expected_values.json").read_text())
    registry = bootstrap_registry()
    field_resolver = FieldResolver(semantic_model)
    reference_date = date.fromisoformat(expected["reference_date"])

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
        signal_gatherer=SignalGatherer(engine, semantic_model, registry, reference_date=reference_date),
        planner=DashboardPlanner(GroqLLMClient(), settings.dashboard_min_components, settings.dashboard_max_components),
        validator=DashboardValidator(registry, field_resolver, settings.dashboard_min_components, settings.dashboard_max_components),
        resolver=DashboardDataResolver(engine),
        field_resolver=field_resolver,
    )

    print(f"reference_date: {reference_date}  (ground truth: {DATA_DIR / 'expected_values.json'})")
    print("expected shape: revenue_growth={:.2f}, order_growth={:.2f}, customer_growth={:.2f} all HEALTHY, but repeat_purchase_rate={:.2f} is a real problem\n".format(
        expected["revenue_growth"], expected["order_growth"], expected["customer_growth"], expected["repeat_purchase_rate"]
    ))

    dashboard = pipeline.run()
    print(dashboard.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
