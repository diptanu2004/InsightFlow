"""
Runs the schema-discovery pipeline against every eval_* dataset under data/raw/ and
scores each one against its matching ground truth file, printing a combined table.

Expects, for each dataset folder `data/raw/<name>/` containing CSV files, a matching
ground truth file at `data/ground_truth/<name>_ground_truth.json`.

Usage:
    uv run python scripts/eval_all.py
    uv run python scripts/eval_all.py --data-dir data/raw --ground-truth-dir data/ground_truth
    uv run python scripts/eval_all.py --pattern "eval_*"
"""
import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from insightflow.evaluation.evaluator import Evaluator
from insightflow.evaluation.ground_truth import GroundTruth
from insightflow.pipeline import SchemaDiscoveryPipeline


@dataclass
class DatasetResult:
    dataset: str
    status: str
    mapping_precision: float | None = None
    mapping_recall: float | None = None
    relationship_precision: float | None = None
    relationship_recall: float | None = None
    error: str | None = None


def discover_datasets(data_dir: Path, ground_truth_dir: Path, pattern: str) -> list[tuple[Path, Path]]:
    """Returns (dataset_dir, ground_truth_path) pairs for every dataset folder that has
    a matching ground truth file. Supports two ground-truth layouts:
      - flat:     data/ground_truth/<name>_ground_truth.json
      - nested:   data/ground_truth/<name>/ground_truth.json
    Datasets with neither are skipped with a printed warning."""
    pairs = []
    for dataset_dir in sorted(data_dir.glob(pattern)):
        if not dataset_dir.is_dir():
            continue
        flat_path = ground_truth_dir / f"{dataset_dir.name}_ground_truth.json"
        nested_path = ground_truth_dir / dataset_dir.name / "ground_truth.json"
        if flat_path.exists():
            pairs.append((dataset_dir, flat_path))
        elif nested_path.exists():
            pairs.append((dataset_dir, nested_path))
        else:
            print(f"[skip] {dataset_dir.name}: no ground truth at {flat_path} or {nested_path}")
    return pairs


def run_one(pipeline: SchemaDiscoveryPipeline, evaluator: Evaluator, dataset_dir: Path, gt_path: Path) -> DatasetResult:
    csv_files = sorted(str(p) for p in dataset_dir.glob("*.csv"))
    if not csv_files:
        return DatasetResult(dataset=dataset_dir.name, status="error", error="no CSV files found")

    try:
        model = pipeline.run(csv_files)
        ground_truth = GroundTruth.load(str(gt_path))
        report = evaluator.evaluate(model, ground_truth)
        return DatasetResult(
            dataset=dataset_dir.name,
            status="ok",
            mapping_precision=report.mapping_precision,
            mapping_recall=report.mapping_recall,
            relationship_precision=report.relationship_precision,
            relationship_recall=report.relationship_recall,
        )
    except Exception as exc:  # keep going — one bad dataset shouldn't kill the run
        return DatasetResult(dataset=dataset_dir.name, status="error", error=str(exc))


def print_table(results: list[DatasetResult]) -> None:
    header = f"{'dataset':<15} {'map P':>7} {'map R':>7} {'rel P':>7} {'rel R':>7}  status"
    print(header)
    print("-" * len(header))
    for r in results:
        if r.status == "ok":
            print(
                f"{r.dataset:<15} {r.mapping_precision:>7.2f} {r.mapping_recall:>7.2f} "
                f"{r.relationship_precision:>7.2f} {r.relationship_recall:>7.2f}  ok"
            )
        else:
            print(f"{r.dataset:<15} {'-':>7} {'-':>7} {'-':>7} {'-':>7}  ERROR: {r.error}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate POC 1 against all eval_* ground-truth datasets")
    parser.add_argument("--data-dir", default="data/raw", help="Directory containing dataset subfolders")
    parser.add_argument("--ground-truth-dir", default="data/ground_truth", help="Directory containing *_ground_truth.json files")
    parser.add_argument("--pattern", default="eval_*", help="Glob pattern for dataset subfolder names")
    parser.add_argument("--out", default="eval_results.json", help="Where to write the combined results JSON")
    args = parser.parse_args()

    data_dir, ground_truth_dir = Path(args.data_dir), Path(args.ground_truth_dir)
    dataset_pairs = discover_datasets(data_dir, ground_truth_dir, args.pattern)

    if not dataset_pairs:
        print(f"No datasets with matching ground truth found under {data_dir} (pattern: {args.pattern})")
        return

    pipeline = SchemaDiscoveryPipeline()
    evaluator = Evaluator()

    results = [run_one(pipeline, evaluator, dataset_dir, gt_path) for dataset_dir, gt_path in dataset_pairs]

    print()
    print_table(results)

    Path(args.out).write_text(json.dumps([asdict(r) for r in results], indent=2))
    print(f"\nFull results written to {args.out}")


if __name__ == "__main__":
    main()
