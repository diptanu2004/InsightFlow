"""
CLI entry point for POC 2. Mirrors exactly what a future FastAPI route will do —
this is intentional, same reasoning as POC 1's scripts/run_poc1.py:

    pipeline = build_pipeline(semantic_model, registry, data_dir)
    result = pipeline.run(query)

Usage:
    uv run python scripts/run_poc2.py \\
        --query data/queries/revenue.json \\
        --semantic-model data/semantic_model.json \\
        --data-dir data/raw/sample

    # Run the evaluation harness over a fixtures directory instead of a single query:
    uv run python scripts/run_poc2.py --fixtures data/fixtures/ \\
        --semantic-model data/semantic_model.json --data-dir data/raw/sample
"""
import argparse
import json
from pathlib import Path

from insightflow.config import settings
from insightflow.evaluation import Evaluator, MetricFixture
from insightflow_core.models import AnalyticalQuery, SemanticModel
from insightflow_core.pipeline import build_pipeline
from insightflow.registry import bootstrap_registry


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the POC 2 analytics engine")
    parser.add_argument("--query", help="Path to a JSON file containing one AnalyticalQuery")
    parser.add_argument("--fixtures", help="Path to a directory of MetricFixture JSON files (runs the eval harness instead)")
    parser.add_argument("--semantic-model", required=True, help="Path to POC 1's semantic_model.json")
    parser.add_argument("--data-dir", required=True, help="Directory containing the semantic-mapped source CSVs")
    args = parser.parse_args()

    if not args.query and not args.fixtures:
        parser.error("pass either --query or --fixtures")

    semantic_model = SemanticModel(**json.loads(Path(args.semantic_model).read_text()))
    registry = bootstrap_registry()

    pipeline = build_pipeline(
        semantic_model,
        registry,
        args.data_dir,
        duckdb_path=settings.duckdb_path,
        query_timeout_seconds=settings.query_timeout_seconds,
        max_row_limit=settings.max_row_limit,
    )

    if args.query:
        query = AnalyticalQuery(**json.loads(Path(args.query).read_text()))
        result = pipeline.run(query)
        print(result.model_dump_json(indent=2))
    else:
        fixtures = [MetricFixture.load(str(p)) for p in sorted(Path(args.fixtures).glob("*.json"))]
        report = Evaluator(pipeline).evaluate(fixtures)
        print(report.summary())


if __name__ == "__main__":
    main()
