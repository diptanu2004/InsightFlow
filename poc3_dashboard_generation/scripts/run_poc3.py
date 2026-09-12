"""
CLI entry point for POC 3. Mirrors exactly what a future FastAPI route will do -- same reasoning
as scripts/run_poc2.py:

    pipeline = build_dashboard_pipeline(semantic_model, registry, data_dir, llm_client)
    dashboard = pipeline.run()

Usage:
    uv run python scripts/run_poc3.py \\
        --semantic-model data/semantic_model.json --data-dir data/raw/sample

Requires GROQ_API_KEY (env var or .env) -- DashboardPlanner makes a real LLM call, unlike POC 2.
"""
import argparse
import json
from pathlib import Path

from insightflow.dashboard.pipeline import build_dashboard_pipeline
from insightflow.llm.groq_client import GroqLLMClient
from insightflow_core.models import SemanticModel
from insightflow.registry import bootstrap_registry


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the POC 3 dashboard generation pipeline")
    parser.add_argument("--semantic-model", required=True, help="Path to POC 1's semantic_model.json")
    parser.add_argument("--data-dir", required=True, help="Directory containing the semantic-mapped source CSVs")
    args = parser.parse_args()

    semantic_model = SemanticModel(**json.loads(Path(args.semantic_model).read_text()))
    registry = bootstrap_registry()

    pipeline = build_dashboard_pipeline(semantic_model, registry, args.data_dir, GroqLLMClient())
    dashboard = pipeline.run()
    print(dashboard.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
