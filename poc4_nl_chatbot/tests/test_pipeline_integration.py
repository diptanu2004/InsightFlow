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


def test_a_caveated_result_is_explained_deterministically_without_the_insight_llm(
    semantic_model, registry, engine, make_fake_llm_client
):
    """Phase 8: on a real Olist dashboard an LLM told not to draw conclusions from a caveated value
    did anyway, so a caveated answer's explanation is never left to an LLM."""
    from insightflow_core.models import MetricResult, QueryMetadata

    from insightflow_chatbot.models.result import QuestionResult

    intent = QuestionIntent(answerable=True, metric_name="repeat_purchase_rate", operation=QuestionOperation.AGGREGATE)
    insight_llm = make_fake_llm_client(RuntimeError("the insight LLM must not be called for a caveated result"))
    pipeline = _build_pipeline(semantic_model, registry, engine, make_fake_llm_client(intent), insight_llm)
    caveat = 'every "customer_id" has at most one "orders_per_customer", so this rate is 0 by construction.'

    class _CaveatedExecutor:
        def execute(self, resolved):
            return QuestionResult(
                metric_result=MetricResult(
                    metric_name="repeat_purchase_rate",
                    shape="scalar",
                    value=0.0,
                    metadata=QueryMetadata(sql="...", execution_time_ms=1.0, row_count=1),
                    caveats=[caveat],
                )
            )

    pipeline.executor = _CaveatedExecutor()

    answer = pipeline.answer("What is our repeat purchase rate?")

    assert answer.refused is False
    assert insight_llm.last_prompt is None
    assert caveat in answer.explanation and "can't be interpreted" in answer.explanation


def test_an_answer_carries_the_exact_queries_run_and_the_date_it_is_measured_from(
    semantic_model, registry, engine, make_fake_llm_client
):
    """Phase 8 M5: "last quarter" alone doesn't say which quarter. With data through August, it's Q2."""
    from insightflow_chatbot.services.insight import _ExplanationOutput

    intent = QuestionIntent(
        answerable=True, metric_name="revenue", operation=QuestionOperation.AGGREGATE, time_expression=TimeExpression.LAST_QUARTER
    )
    pipeline = _build_pipeline(
        semantic_model, registry, engine, make_fake_llm_client(intent), make_fake_llm_client(_ExplanationOutput(explanation="ok"))
    )

    answer = pipeline.answer("What was revenue last quarter?")

    assert answer.data_through == REFERENCE_DATE
    assert len(answer.queries) == 1
    window = answer.queries[0].time_filter
    assert (window.start_date, window.end_date) == (date(2025, 10, 1), date(2025, 12, 31))


def test_a_period_comparison_on_a_cut_off_list_is_refused_not_zero_filled(
    semantic_model, registry, engine, make_fake_llm_client
):
    """Phase 8 M5: ~2,100 Olist cities per quarter against a 1,000-row cap. Zero-filling the cities
    cut from one list reported Brasilia's 442 orders as '461 -> 0'."""
    from insightflow_core.models import MetricResult, QueryMetadata

    from insightflow_chatbot.services.executor import QuestionExecutor
    from insightflow_chatbot.services.result_differ import ResultDiffer

    class _CappedEngine:
        def run(self, query):
            rows = [{"region": f"city{i}", "value": 1.0} for i in range(1000)]
            return MetricResult(
                metric_name="orders", shape="grouped", rows=rows,
                metadata=QueryMetadata(sql="...", execution_time_ms=1.0, row_count=1000), truncated=True,
            )

    intent = QuestionIntent(
        answerable=True, metric_name="revenue", operation=QuestionOperation.GROWTH_BY_DIMENSION,
        dimension="region", time_expression=TimeExpression.LAST_QUARTER,
    )
    insight_llm = make_fake_llm_client(RuntimeError("must not explain an incomplete comparison"))
    pipeline = _build_pipeline(semantic_model, registry, engine, make_fake_llm_client(intent), insight_llm)
    pipeline.executor = QuestionExecutor(_CappedEngine(), ResultDiffer())

    answer = pipeline.answer("Which region's revenue fell the most last quarter?")

    assert answer.refused is True
    assert "incomplete lists" in answer.reason
    assert insight_llm.last_prompt is None


class _ByOutputSchema:
    """One fake LLM for the whole wired pipeline: the planner and the insight generator share a client."""

    def __init__(self, *outputs):
        self.outputs = {type(o): o for o in outputs}

    def generate_structured(self, prompt, output_schema):
        return self.outputs[output_schema]


def _without_a_date_column(semantic_model):
    # Real case: a 4-file Olist upload without the orders file, whose only date column
    # (shipping_limit_date) was still awaiting review -- so the trusted model had no transaction_date.
    for entity in semantic_model.entities:
        entity.fields = [f for f in entity.fields if f.name != "transaction_date"]
    return semantic_model


def _build_wired_without_dates(semantic_model, registry, *outputs):
    from pathlib import Path

    from insightflow_core.pipeline import build_pipeline

    from insightflow_chatbot.pipeline import build_question_answering_pipeline

    model = _without_a_date_column(semantic_model)
    data_dir = str(Path(__file__).parent.parent / "data" / "raw" / "sample")
    engine = build_pipeline(model, registry, data_dir)
    return build_question_answering_pipeline(model, registry, engine, _ByOutputSchema(*outputs), data_dir)


def test_a_dataset_without_a_date_column_still_answers_questions_over_all_of_the_data(semantic_model, registry):
    intent = QuestionIntent(answerable=True, metric_name="revenue", operation=QuestionOperation.AGGREGATE)
    pipeline = _build_wired_without_dates(semantic_model, registry, intent, _ExplanationOutput(explanation="Revenue is 790."))

    answer = pipeline.answer("What is our total revenue?")

    assert answer.refused is False
    assert answer.result.metric_result.value == 790.0
    assert answer.data_through is None


@pytest.mark.parametrize(
    "intent",
    [
        QuestionIntent(answerable=True, metric_name="revenue", operation=QuestionOperation.AGGREGATE, time_expression=TimeExpression.LAST_QUARTER),
        QuestionIntent(answerable=True, metric_name="revenue_growth", operation=QuestionOperation.GROWTH, time_expression=TimeExpression.LAST_QUARTER),
        QuestionIntent(
            answerable=True, metric_name="revenue", operation=QuestionOperation.GROWTH_BY_DIMENSION,
            dimension="category", time_expression=TimeExpression.LAST_MONTH,
        ),
    ],
    ids=["aggregate-last-quarter", "growth", "growth-by-dimension"],
)
def test_a_dataset_without_a_date_column_refuses_period_questions_with_a_reason(semantic_model, registry, intent):
    pipeline = _build_wired_without_dates(semantic_model, registry, intent, _ExplanationOutput(explanation="never used"))

    answer = pipeline.answer("What was revenue last quarter?")

    assert answer.refused is True
    assert "no confirmed date column" in answer.reason
    assert answer.result is None


class _ByCall:
    """Planner output first, explanation second -- the wired pipeline shares one client."""

    def __init__(self, intent, explanation="x"):
        self.intent, self.explanation, self.prompts = intent, _ExplanationOutput(explanation=explanation), []

    def generate_structured(self, prompt, output_schema):
        self.prompts.append(prompt)
        return self.intent if output_schema is QuestionIntent else self.explanation


def _filtered_pipeline(semantic_model, registry, engine, llm):
    from insightflow_chatbot.services.insight import InsightGenerator

    return QuestionAnsweringPipeline(
        semantic_model=semantic_model, registry=registry, engine=engine, planner=QuestionPlanner(llm),
        assembler=QueryAssembler(TimeExpressionResolver(reference_date=REFERENCE_DATE, min_date=MIN_DATE)),
        validator=QuestionValidator(ASTValidator(registry, semantic_model, 1000)),
        executor=QuestionExecutor(engine, ResultDiffer()), insight_generator=InsightGenerator(llm),
    )


def test_a_total_over_specific_values_is_computed_by_the_engine(semantic_model, registry, engine):
    """Real Olist: "total customers in AL, RN and CE" was answered with every region and summed by the LLM."""
    intent = QuestionIntent(
        answerable=True, metric_name="revenue", operation=QuestionOperation.AGGREGATE, dimension_values=["north"], filter_dimension="region"
    )
    llm = _ByCall(intent, explanation="Revenue in the North region is 380.")

    answer = _filtered_pipeline(semantic_model, registry, engine, llm).answer("What is revenue in the north?")

    assert answer.refused is False
    assert answer.result.metric_result.value == 380.0  # C1's three orders + C3's one
    assert [(f.dimension, f.values) for f in answer.queries[0].filters] == [("region", ["North"])]  # as spelled in the data
    assert answer.explanation == "Revenue in the North region is 380."
    assert "only rows where region is one of: North" in llm.prompts[-1]


def test_the_planners_guess_of_dimension_is_checked_against_the_data(semantic_model, registry, engine):
    intent = QuestionIntent(
        answerable=True, metric_name="revenue", operation=QuestionOperation.AGGREGATE, dimension_values=["North"], filter_dimension="category"
    )

    answer = _filtered_pipeline(semantic_model, registry, engine, _ByCall(intent)).answer("revenue for North?")

    assert answer.refused is False
    assert answer.queries[0].filters[0].dimension == "region"
    assert answer.result.metric_result.value == 380.0


def test_a_value_missing_from_the_named_dimension_is_refused_not_reported_as_zero(semantic_model, registry, engine):
    intent = QuestionIntent(
        answerable=True, metric_name="revenue", operation=QuestionOperation.AGGREGATE,
        dimension_values=["North", "Atlantis"], filter_dimension="region",
    )

    answer = _filtered_pipeline(semantic_model, registry, engine, _ByCall(intent)).answer("revenue in North and Atlantis?")

    assert answer.refused is True
    assert answer.reason.startswith('There\'s no region called "Atlantis" in this data (searched the "region" column)')
    assert answer.result is None


def test_a_value_found_nowhere_names_the_dimensions_searched(semantic_model, registry, engine):
    intent = QuestionIntent(answerable=True, metric_name="orders", operation=QuestionOperation.AGGREGATE, dimension_values=["Atlantis"])

    answer = _filtered_pipeline(semantic_model, registry, engine, _ByCall(intent)).answer("orders in Atlantis?")

    assert answer.refused is True
    assert 'Couldn\'t find "Atlantis" in any of:' in answer.reason and "region" in answer.reason


def test_a_period_comparison_is_filtered_in_both_periods(semantic_model, registry, engine):
    intent = QuestionIntent(
        answerable=True, metric_name="revenue", operation=QuestionOperation.GROWTH_BY_DIMENSION, dimension="region",
        time_expression=TimeExpression.LAST_QUARTER, dimension_values=["Gadgets"], filter_dimension="category",
    )

    answer = _filtered_pipeline(semantic_model, registry, engine, _ByCall(intent)).answer("Which region's gadget revenue fell last quarter?")

    assert answer.refused is False
    assert all(q.filters and q.filters[0].values == ["Gadgets"] for q in answer.queries)
    assert len(answer.queries) == 2
