"""
CLI entry point for POC 4. Mirrors exactly what a future FastAPI route will do -- same reasoning
as scripts/run_poc3.py:

    pipeline = build_question_answering_pipeline(semantic_model, registry, engine, llm_client, data_dir)
    answer = pipeline.answer(question)

Usage:
    uv run python scripts/run_poc4.py \\
        --semantic-model data/semantic_model.json --data-dir data/raw/sample \\
        --question "What is our total revenue?"

Requires GROQ_API_KEY (env var or .env) -- QuestionPlanner and InsightGenerator both make real
LLM calls, unlike POC 2.
"""
import argparse
import json
from pathlib import Path

from insightflow_core.models import SemanticModel
from insightflow_core.pipeline import build_pipeline

from insightflow.llm.groq_client import GroqLLMClient
from insightflow.pipeline import build_question_answering_pipeline
from insightflow.registry import bootstrap_registry


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the POC 4 natural-language chatbot pipeline")
    parser.add_argument("--semantic-model", required=True, help="Path to POC 1's semantic_model.json")
    parser.add_argument("--data-dir", required=True, help="Directory containing the semantic-mapped source CSVs")
    parser.add_argument("--question", required=True, help="A natural-language business question")
    args = parser.parse_args()

    semantic_model = SemanticModel(**json.loads(Path(args.semantic_model).read_text()))
    registry = bootstrap_registry()
    engine = build_pipeline(semantic_model, registry, args.data_dir)

    pipeline = build_question_answering_pipeline(semantic_model, registry, engine, GroqLLMClient(), args.data_dir)
    answer = pipeline.answer(args.question)
    print(answer.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
