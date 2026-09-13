"""M4: real-Groq evaluation run against data/refusal_benchmark.json's 16 fixtures, using POC 4's
own sample dataset (data/semantic_model.json + data/raw/sample) and bootstrap_registry() -- the
benchmark's expected_metric/expected_operation values were written against that registry (see
class_diagram.md's resolved "Refusal benchmark set" question).

Prints each fixture's real planner output alongside the expected one, then the aggregate
AnswerEvaluationReport -- not just the summary, since individual disagreements are what actually
inform whether the planner prompt needs work.

Usage: uv run python scripts/run_m4_evaluation.py   (needs GROQ_API_KEY, e.g. via .env)
"""
import json
import sys
import time
from pathlib import Path

# Windows' default console codepage (cp1252) can't encode some characters real LLM output
# contains (e.g. a narrow no-break space) -- reconfigure stdout to UTF-8 so a cosmetic encoding
# error doesn't crash a real Groq run partway through.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from groq import RateLimitError
from insightflow_core.models import SemanticModel
from insightflow_core.pipeline import build_pipeline

from insightflow.llm.groq_client import GroqLLMClient
from insightflow.pipeline import build_question_answering_pipeline
from insightflow.registry import bootstrap_registry
from insightflow.services.evaluator import AnswerEvaluator
from insightflow.services.fixtures import load_fixtures

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _answer_with_retry(pipeline, question: str, max_attempts: int = 6):
    # This POC's own on-demand Groq tier has an 8000 TPM budget -- easy to trip with 16
    # fixtures x up to 2 real calls each (planner + insight). Retrying here is a script-level
    # concern for running this specific benchmark, not a QuestionAnsweringPipeline feature.
    for attempt in range(max_attempts):
        try:
            return pipeline.answer(question)
        except RateLimitError:
            wait_seconds = 20 * (attempt + 1)
            print(f"    (rate limited, waiting {wait_seconds}s before retry {attempt + 1}/{max_attempts})")
            time.sleep(wait_seconds)
    raise RuntimeError(f"still rate limited after {max_attempts} attempts")


def main() -> None:
    semantic_model = SemanticModel(**json.loads((DATA_DIR / "semantic_model.json").read_text()))
    registry = bootstrap_registry()
    engine = build_pipeline(semantic_model, registry, str(DATA_DIR / "raw" / "sample"))

    llm_client = GroqLLMClient()
    pipeline = build_question_answering_pipeline(semantic_model, registry, engine, llm_client, str(DATA_DIR / "raw" / "sample"))

    fixtures = load_fixtures(str(DATA_DIR / "refusal_benchmark.json"))

    print(f"Running {len(fixtures)} fixtures against real Groq ({llm_client._llm.model_name})...\n")
    pairs = []
    for fixture in fixtures:
        answer = _answer_with_retry(pipeline, fixture.question)
        pairs.append((fixture, answer))
        actual_answerable = not answer.refused
        # Small pacing gap between fixtures, independent of the retry backoff above -- reduces
        # how often the retry path is needed at all, given the 8000 TPM budget.
        time.sleep(3)
        match = "OK" if actual_answerable == fixture.expected_answerable else "MISMATCH"
        print(f"[{match}] {fixture.question!r}")
        print(f"    expected_answerable={fixture.expected_answerable} expected_metric={fixture.expected_metric} expected_operation={fixture.expected_operation}")
        if answer.intent is not None:
            print(f"    actual: answerable={answer.intent.answerable} metric={answer.intent.metric_name} operation={answer.intent.operation} dimension={answer.intent.dimension} time_expression={answer.intent.time_expression}")
        if answer.refused:
            print(f"    reason: {answer.reason}")
        else:
            if answer.result.metric_result is not None:
                print(f"    value: {answer.result.metric_result.value}")
            else:
                print(f"    category_deltas: {[d.model_dump() for d in answer.result.category_deltas]}")
            print(f"    explanation: {answer.explanation}")
        print()

    evaluator = AnswerEvaluator(pipeline)
    report = evaluator.score(pairs)
    print("=" * 70)
    print(report.summary())


if __name__ == "__main__":
    main()
