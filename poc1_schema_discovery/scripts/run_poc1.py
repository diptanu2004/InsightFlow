"""
CLI entry point for POC 1. Mirrors exactly what a future FastAPI route will do —
this is intentional, so wiring up the route later is a thin wrapper, not a rewrite:

    pipeline = SchemaDiscoveryPipeline()
    model = pipeline.run(filepaths)

Usage:
    uv run python scripts/run_poc1.py data/raw/sample/orders.csv data/raw/sample/customers.csv \
        data/raw/sample/products.csv --ground-truth data/ground_truth/sample_ground_truth.json
"""
import argparse
from pathlib import Path

from insightflow.evaluation.evaluator import Evaluator
from insightflow.evaluation.ground_truth import GroundTruth
from insightflow.pipeline import SchemaDiscoveryPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the POC 1 schema discovery pipeline")
    parser.add_argument("files", nargs="+", help="Paths to CSV/Excel files")
    parser.add_argument("--ground-truth", help="Path to a ground truth JSON file for evaluation")
    parser.add_argument("--out", default="semantic_model.json", help="Output path for the semantic model JSON")
    args = parser.parse_args()

    pipeline = SchemaDiscoveryPipeline()
    model = pipeline.run(args.files)

    Path(args.out).write_text(model.model_dump_json(indent=2))
    print(f"Semantic model written to {args.out}\n")
    print(model.model_dump_json(indent=2))

    if args.ground_truth:
        ground_truth = GroundTruth.load(args.ground_truth)
        report = Evaluator().evaluate(model, ground_truth)
        print("\nEvaluation:")
        print(report.summary())


if __name__ == "__main__":
    main()
