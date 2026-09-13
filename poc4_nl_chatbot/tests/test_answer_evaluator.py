from datetime import date

import pytest

from insightflow_core.validation import ASTValidator

from insightflow.llm.client import LLMClient
from insightflow.models.evaluation import QuestionFixture
from insightflow.models.intent import QuestionIntent, QuestionOperation
from insightflow.models.time_expression import TimeExpression
from insightflow.pipeline import QuestionAnsweringPipeline
from insightflow.services.evaluator import AnswerEvaluator
from insightflow.services.executor import QuestionExecutor
from insightflow.services.insight import _ExplanationOutput
from insightflow.services.planner import QuestionPlanner
from insightflow.services.query_assembler import QueryAssembler
from insightflow.services.result_differ import ResultDiffer
from insightflow.services.time_resolver import TimeExpressionResolver
from insightflow.services.validator import QuestionValidator
from insightflow.services.insight import InsightGenerator

REFERENCE_DATE = date(2026, 2, 15)
MIN_DATE = date(2025, 11, 1)


class ScriptedLLMClient(LLMClient):
    """Returns each item in `outputs` in order, one per call -- unlike FakeLLMClient (always the
    same fixed output), this lets one pipeline answer several DIFFERENT questions in sequence,
    which AnswerEvaluator needs when scored against more than one QuestionFixture."""

    def __init__(self, outputs: list):
        self.outputs = list(outputs)
        self.calls = 0

    def generate_structured(self, prompt, output_schema):
        output = self.outputs[self.calls]
        self.calls += 1
        return output


def _build_pipeline(semantic_model, registry, engine, planner_outputs, insight_outputs):
    time_resolver = TimeExpressionResolver(reference_date=REFERENCE_DATE, min_date=MIN_DATE)
    return QuestionAnsweringPipeline(
        semantic_model=semantic_model,
        registry=registry,
        engine=engine,
        planner=QuestionPlanner(ScriptedLLMClient(planner_outputs)),
        assembler=QueryAssembler(time_resolver),
        validator=QuestionValidator(ASTValidator(registry, semantic_model, 1000)),
        executor=QuestionExecutor(engine, ResultDiffer()),
        insight_generator=InsightGenerator(ScriptedLLMClient(insight_outputs)),
    )


def test_evaluator_scores_a_correct_answerable_a_correct_refusal_and_a_hallucination(semantic_model, registry, engine):
    fixtures = [
        QuestionFixture(
            question="What is our total revenue?",
            expected_answerable=True,
            expected_metric="revenue",
            expected_operation=QuestionOperation.AGGREGATE,
            expected_value=790.0,
        ),
        QuestionFixture(question="What is our conversion rate?", expected_answerable=False),
        QuestionFixture(question="How many customers do we have?", expected_answerable=False),
    ]
    planner_outputs = [
        QuestionIntent(
            answerable=True, metric_name="revenue", operation=QuestionOperation.AGGREGATE, time_expression=TimeExpression.ALL_TIME
        ),
        QuestionIntent(answerable=False, reason="no session data available"),
        # This one "hallucinates" answerable=True for a fixture the ground truth says should be
        # refused -- the case AnswerEvaluator counts as a hallucination, not just a wrong score.
        QuestionIntent(answerable=True, metric_name="customers", operation=QuestionOperation.AGGREGATE),
    ]
    insight_outputs = [
        _ExplanationOutput(explanation="Total revenue is $790."),
        _ExplanationOutput(explanation="We have some customers."),
    ]

    pipeline = _build_pipeline(semantic_model, registry, engine, planner_outputs, insight_outputs)
    report = AnswerEvaluator(pipeline).evaluate(fixtures)

    assert report.total == 3
    assert report.refusal_correct == 2  # fixture 1 (answerable, correctly answered) + fixture 2 (refused, correctly refused)
    assert report.hallucinations == 1  # fixture 3
    assert report.intent_correct == 1  # only fixture 1 has expected_answerable=True
    assert report.plan_correct == 1
    assert report.numerically_correct == 1


def test_evaluator_report_summary_is_human_readable(semantic_model, registry, engine):
    fixtures = [QuestionFixture(question="What is our conversion rate?", expected_answerable=False)]
    pipeline = _build_pipeline(
        semantic_model, registry, engine, [QuestionIntent(answerable=False, reason="no session data")], []
    )
    report = AnswerEvaluator(pipeline).evaluate(fixtures)
    assert "1 questions" in report.summary()
    assert "refusal 1/1" in report.summary()
