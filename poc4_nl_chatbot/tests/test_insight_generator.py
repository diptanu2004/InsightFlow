from insightflow_core.models import MetricResult, QueryMetadata

from insightflow_chatbot.models.result import CategoryDelta, QuestionResult
from insightflow_chatbot.services.insight import InsightGenerator, _ExplanationOutput


def _metadata():
    return QueryMetadata(sql="", execution_time_ms=0.0, row_count=1)


def test_explain_returns_the_fake_llms_explanation(make_fake_llm_client):
    fixed = _ExplanationOutput(explanation="Revenue was $790.")
    generator = InsightGenerator(make_fake_llm_client(fixed))
    result = QuestionResult(metric_result=MetricResult(metric_name="revenue", shape="scalar", value=790.0, metadata=_metadata()))

    explanation = generator.explain("What is total revenue?", result)

    assert explanation == "Revenue was $790."


def test_explain_prompt_includes_the_scalar_value(make_fake_llm_client):
    fake_client = make_fake_llm_client(_ExplanationOutput(explanation="x"))
    generator = InsightGenerator(fake_client)
    result = QuestionResult(metric_result=MetricResult(metric_name="revenue", shape="scalar", value=790.0, metadata=_metadata()))

    generator.explain("What is total revenue?", result)

    assert "revenue = 790" in fake_client.last_prompt


def test_explain_prompt_includes_category_deltas(make_fake_llm_client):
    fake_client = make_fake_llm_client(_ExplanationOutput(explanation="x"))
    generator = InsightGenerator(fake_client)
    deltas = [CategoryDelta(dimension_value="Gadgets", current_value=100.0, comparison_value=200.0, delta=-100.0, pct_change=-0.5)]
    result = QuestionResult(category_deltas=deltas)

    generator.explain("Which category declined?", result)

    assert "Gadgets" in fake_client.last_prompt
    assert "-100.00" in fake_client.last_prompt


def test_a_caveated_result_is_passed_through_with_an_instruction_not_to_interpret_it():
    """Phase 8: on real Olist repeat_purchase_rate is 0 only because customer_id is per-order there,
    and a dashboard narrative drew a business conclusion from it."""
    from insightflow_core.models import MetricResult, QueryMetadata

    from insightflow_chatbot.models.result import QuestionResult
    from insightflow_chatbot.services.insight import InsightGenerator

    result = QuestionResult(
        metric_result=MetricResult(
            metric_name="repeat_purchase_rate",
            shape="scalar",
            value=0.0,
            metadata=QueryMetadata(sql="...", execution_time_ms=1.0, row_count=1),
            caveats=["every customer_id has at most one order, so this rate is 0 by construction."],
        )
    )

    prompt = InsightGenerator._build_prompt("what is our repeat purchase rate?", result)

    assert "CAVEAT: every customer_id has at most one order" in prompt
    assert "do not draw any conclusion from that value" in prompt


def test_a_large_grouped_result_is_summarized_not_sent_whole():
    """1,000 Olist cities in one prompt was 16k tokens; the provider rejected it (413)."""
    from insightflow_core.models import MetricResult, QueryMetadata

    from insightflow_chatbot.models.result import QuestionResult
    from insightflow_chatbot.services.insight import InsightGenerator

    rows = [{"region": f"city{i}", "value": float(1000 - i)} for i in range(1000)]
    result = QuestionResult(
        metric_result=MetricResult(
            metric_name="orders", shape="grouped", rows=rows, truncated=True,
            metadata=QueryMetadata(sql="...", execution_time_ms=1.0, row_count=1000),
        )
    )

    prompt = InsightGenerator._build_prompt("orders by city?", result)

    assert "city19" in prompt and "city20'" not in prompt
    assert "980 more groups not listed" in prompt
    assert "do not state totals" in prompt


def test_scalar_values_reach_the_explanation_formatted_not_as_raw_floats():
    from insightflow_core.models import MetricResult, QueryMetadata

    from insightflow_chatbot.models.result import QuestionResult
    from insightflow_chatbot.services.insight import InsightGenerator

    result = QuestionResult(
        metric_result=MetricResult(
            metric_name="revenue", shape="scalar", value=3338648.129999977, format="money",
            metadata=QueryMetadata(sql="...", execution_time_ms=1.0, row_count=1),
        )
    )
    prompt = InsightGenerator._build_prompt("revenue last quarter?", result)
    assert "revenue = 3,338,648.13" in prompt and "129999977" not in prompt
