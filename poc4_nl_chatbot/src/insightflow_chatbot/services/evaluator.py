"""AnswerEvaluator -- see class_diagram.md's Evaluation classes and its resolved "Refusal
benchmark set" question: fixtures cover both the answerable case (POC 1/2/3's usual ground-truth
pattern) and the unanswerable case, from day one rather than deferred.

Mirrors POC 2's own Evaluator/MetricFixture/EvaluationReport shape (numerically-correctness-driven,
mechanically checkable) more than POC 3's DashboardEvaluator (which needs a human rubric for
"usefulness") -- a chatbot answer's correctness IS mechanically checkable against a known metric,
value, and refusal expectation, the same way POC 2's metric fixtures are.
"""
from typing import TYPE_CHECKING

from insightflow_chatbot.models.evaluation import AnswerEvaluationReport, QuestionFixture
from insightflow_chatbot.models.result import Answer

if TYPE_CHECKING:
    # Import-time only -- insightflow.pipeline itself imports insightflow.services (for
    # QuestionExecutor et al.), so a top-level import here would be circular. AnswerEvaluator only
    # needs QuestionAnsweringPipeline for the type hint below.
    from insightflow_chatbot.pipeline import QuestionAnsweringPipeline


class AnswerEvaluator:
    def __init__(self, pipeline: "QuestionAnsweringPipeline"):
        self.pipeline = pipeline

    def evaluate(self, fixtures: list[QuestionFixture]) -> AnswerEvaluationReport:
        pairs = [(fixture, self.pipeline.answer(fixture.question)) for fixture in fixtures]
        return self.score(pairs)

    def score(self, pairs: list[tuple[QuestionFixture, Answer]]) -> AnswerEvaluationReport:
        """Scores fixture/answer pairs that were already produced elsewhere -- lets a caller
        (e.g. a script that also wants to print each real answer) get both without running the
        pipeline, and its real LLM calls, twice."""
        intent_correct = 0
        plan_correct = 0
        numerically_correct = 0
        refusal_correct = 0
        hallucinations = 0

        for fixture, answer in pairs:
            actually_answerable = not answer.refused

            if actually_answerable == fixture.expected_answerable:
                refusal_correct += 1
            elif actually_answerable and not fixture.expected_answerable:
                # The planner answered a question it should have refused -- the one outcome this
                # evaluator treats as a hallucination risk, not just a wrong classification,
                # since it means a number was produced and explained for a question the ground
                # truth says the platform cannot actually answer.
                hallucinations += 1

            if not fixture.expected_answerable:
                continue

            intent = answer.intent
            if intent is not None and intent.answerable and intent.metric_name == fixture.expected_metric:
                intent_correct += 1
                if fixture.expected_operation is None or intent.operation == fixture.expected_operation:
                    plan_correct += 1

            if fixture.expected_value is not None and answer.result is not None and answer.result.metric_result is not None:
                if answer.result.metric_result.value == fixture.expected_value:
                    numerically_correct += 1

        return AnswerEvaluationReport(
            total=len(pairs),
            intent_correct=intent_correct,
            plan_correct=plan_correct,
            numerically_correct=numerically_correct,
            refusal_correct=refusal_correct,
            hallucinations=hallucinations,
        )
