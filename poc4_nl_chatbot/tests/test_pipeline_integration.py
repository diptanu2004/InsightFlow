from datetime import date

import pytest

from insightflow_core.compilation import FieldResolver
from insightflow_core.validation import ASTValidator

from insightflow_chatbot.models.intent import QuestionIntent, QuestionOperation
from insightflow_chatbot.models.time_expression import TimeExpression
from insightflow_chatbot.pipeline import QuestionAnsweringPipeline
from insightflow_chatbot.services.executor import QuestionExecutor
from insightflow_chatbot.services.insight import _ExplanationOutput
from insightflow_chatbot.services.planner import QuestionPlanner
from insightflow_chatbot.services.query_assembler import QueryAssembler
from insightflow_chatbot.services.result_differ import ResultDiffer
from insightflow_chatbot.services.time_resolver import TimeExpressionResolver
from insightflow_chatbot.services.validator import QuestionValidator

# Same real date range as test_question_executor_integration.py's sample dataset.
REFERENCE_DATE = date(2026, 2, 15)
MIN_DATE = date(2025, 11, 1)


def _build_pipeline(semantic_model, registry, engine, planner_llm, insight_llm):
    from insightflow_chatbot.services.insight import InsightGenerator

    time_resolver = TimeExpressionResolver(reference_date=REFERENCE_DATE, min_date=MIN_DATE)
    return QuestionAnsweringPipeline(
        semantic_model=semantic_model,
        registry=registry,
        engine=engine,
        planner=QuestionPlanner(planner_llm),
        assembler=QueryAssembler(time_resolver),
        validator=QuestionValidator(ASTValidator(registry, semantic_model, 1000)),
        executor=QuestionExecutor(engine, ResultDiffer()),
        insight_generator=InsightGenerator(insight_llm),
    )


def test_planner_refusal_short_circuits_before_any_execution_or_insight_call(
    semantic_model, registry, engine, make_fake_llm_client
):
    refusal = QuestionIntent(answerable=False, reason="conversion rate needs session data this dataset doesn't have")
    insight_llm = make_fake_llm_client(RuntimeError("should never be called"))
    pipeline = _build_pipeline(semantic_model, registry, engine, make_fake_llm_client(refusal), insight_llm)

    answer = pipeline.answer("What is our conversion rate?")

    assert answer.refused is True
    assert answer.reason == refusal.reason
    assert answer.explanation == refusal.reason
    assert answer.result is None


def test_validator_rejection_short_circuits_before_execution_or_insight_call(
    semantic_model, registry, engine, make_fake_llm_client
):
    # Planner "hallucinates" answerable=True for a metric that isn't actually registered --
    # QuestionValidator must catch this independently, per the defense-in-depth pattern.
    bad_intent = QuestionIntent(answerable=True, metric_name="conversion_rate", operation=QuestionOperation.AGGREGATE)
    insight_llm = make_fake_llm_client(RuntimeError("should never be called"))
    pipeline = _build_pipeline(semantic_model, registry, engine, make_fake_llm_client(bad_intent), insight_llm)

    answer = pipeline.answer("What is our conversion rate?")

    assert answer.refused is True
    assert "conversion_rate" in answer.reason
    assert answer.result is None


def test_full_answerable_pipeline_returns_verified_value_and_llm_explanation(
    semantic_model, registry, engine, make_fake_llm_client
):
    intent = QuestionIntent(
        answerable=True, metric_name="revenue", operation=QuestionOperation.AGGREGATE, time_expression=TimeExpression.ALL_TIME
    )
    explanation = _ExplanationOutput(explanation="Total revenue across all time is $790.")
    pipeline = _build_pipeline(semantic_model, registry, engine, make_fake_llm_client(intent), make_fake_llm_client(explanation))

    answer = pipeline.answer("What is our total revenue?")

    assert answer.refused is False
    assert answer.result.metric_result.value == 790.0
    assert answer.explanation == "Total revenue across all time is $790."


def test_full_growth_by_dimension_pipeline_returns_ranked_category_deltas(
    semantic_model, registry, engine, make_fake_llm_client
):
    intent = QuestionIntent(
        answerable=True,
        metric_name="revenue",
        operation=QuestionOperation.GROWTH_BY_DIMENSION,
        dimension="category",
        time_expression=TimeExpression.LAST_QUARTER,
    )
    explanation = _ExplanationOutput(explanation="Gadgets grew the most last quarter.")
    pipeline = _build_pipeline(semantic_model, registry, engine, make_fake_llm_client(intent), make_fake_llm_client(explanation))

    answer = pipeline.answer("Which category drove growth last quarter?")

    assert answer.refused is False
    assert answer.result.category_deltas is not None
    assert {d.dimension_value for d in answer.result.category_deltas} == {"Gadgets", "Home"}
