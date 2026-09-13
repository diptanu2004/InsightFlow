"""Execution-stage and final deliverable shapes -- see hld.md's "Question Intent schema" sketch
and class_diagram.md's CategoryDelta/QuestionResult/Answer classes.
"""
from typing import List, Optional

from pydantic import BaseModel

from insightflow_core.models import MetricResult

from insightflow.models.intent import QuestionIntent


class CategoryDelta(BaseModel):
    """One dimension value's current-vs-comparison comparison, for a GROWTH_BY_DIMENSION answer.

    `current_value`/`comparison_value` are zero-filled (never None) when a dimension value is
    missing from one side -- see class_diagram.md's resolved "category-set mismatch" question.
    `pct_change` is None (not an error) when `comparison_value == 0`, since it's undefined there;
    ranking uses `delta`, not `pct_change`, for exactly this reason.
    """

    dimension_value: str
    current_value: float
    comparison_value: float
    delta: float
    pct_change: Optional[float] = None


class QuestionResult(BaseModel):
    """Exactly one of the two fields is set: `metric_result` for AGGREGATE/GROUP_BY/GROWTH,
    `category_deltas` for GROWTH_BY_DIMENSION. Wraps insightflow_core's MetricResult unmodified
    rather than replacing it -- see class_diagram.md's note on minimal-change pluggability.
    """

    metric_result: Optional[MetricResult] = None
    category_deltas: Optional[List[CategoryDelta]] = None


class Answer(BaseModel):
    """POC 4's actual deliverable. `refused=True` means `result` is None and `explanation` is a
    deterministic reason string (from the planner's own refusal, or from QuestionValidator) --
    InsightGenerator is never invoked for a refused question, see class_diagram.md's resolved
    "Insight LLM prompt scope" question.
    """

    question: str
    refused: bool
    reason: Optional[str] = None
    result: Optional[QuestionResult] = None
    explanation: str
    # The planner's own intent, kept for evaluation/debugging (e.g. AnswerEvaluator checking
    # intent/plan accuracy against a QuestionFixture) -- not needed by an end user of the answer,
    # but cheap to carry since QuestionAnsweringPipeline already has it in hand.
    intent: Optional[QuestionIntent] = None
