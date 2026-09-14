"""Execution-stage and final deliverable shapes -- see hld.md's "Question Intent schema" sketch
and class_diagram.md's CategoryDelta/QuestionResult/Answer classes.
"""
from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel

from insightflow_core.models import AnalyticalQuery, MetricResult

from insightflow_chatbot.models.intent import QuestionIntent


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
    # Display format of `category_deltas` values (a metric_result carries its own).
    format: Literal["money", "count", "percent", "number"] = "number"


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
    # The exact analytical queries that were validated and run -- time windows as concrete dates.
    # Added in Phase 8 M5: `intent.time_expression` alone says "last_quarter", but not which quarter,
    # and on real Olist (data through August 2018) that's Q2, not the Q3 a reader would assume. A
    # number can't be checked without its window. Empty when the question was refused by the planner
    # before any query was assembled.
    queries: List[AnalyticalQuery] = []
    # The latest date relative periods were measured back from (see date_bounds.anchor_date).
    data_through: Optional[date] = None
