import pytest

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


def _grouped_regions():
    rows = [{"region": s, "value": v} for s, v in [("SP", 41746.0), ("RJ", 12852.0), ("CE", 1336.0), ("RN", 485.0), ("AL", 413.0)]]
    return QuestionResult(metric_result=MetricResult(metric_name="customers", shape="grouped", rows=rows, format="count", metadata=_metadata()))


def test_an_explanation_that_adds_up_verified_values_is_withheld(make_fake_llm_client):
    """Real Olist: "total customers in AL, RN and CE" came back as "2234 (AL: 413, RN: 485, CE: 1336)" --
    each count verified, the total computed by the LLM."""
    from insightflow_chatbot.services.insight import WITHHELD_EXPLANATION

    llm = make_fake_llm_client(_ExplanationOutput(explanation="The total number of customers in AL, RN and CE is 2234 (AL: 413, RN: 485, CE: 1336)."))

    assert InsightGenerator(llm).explain("what is the total customers in AL, RN and CE", _grouped_regions()) == WITHHELD_EXPLANATION


@pytest.mark.parametrize(
    "explanation",
    [
        "SP has the most customers with 41,746, followed by RJ with 12,852.",
        "SP leads with about 41.7k customers; the top 2 regions are SP and RJ.",
        "AL, RN and CE have 413, 485 and 1,336 customers respectively.",
    ],
)
def test_an_explanation_citing_only_verified_values_passes(make_fake_llm_client, explanation):
    llm = make_fake_llm_client(_ExplanationOutput(explanation=explanation))

    assert InsightGenerator(llm).explain("customers by region?", _grouped_regions()) == explanation


@pytest.mark.parametrize(
    ("value", "format_", "explanation", "grounded"),
    [
        (16008872.119998764, "money", "Total revenue is 16,008,872.12.", True),
        (16008872.119998764, "money", "Total revenue is about 16.0 million.", True),
        (16008872.119998764, "money", "Total revenue is about 17 million.", False),
        (0.018715821613453962, "percent", "Revenue grew by 1.9% last quarter.", True),
        (-0.1005, "percent", "Revenue declined by 10.1% last quarter.", True),
        (-0.1005, "percent", "Revenue declined by 12% last quarter.", False),
        (160.9886, "money", "Average order value is 160.99, or roughly 320 for two orders.", False),
    ],
)
def test_scalar_explanations_are_checked_against_the_verified_value(make_fake_llm_client, value, format_, explanation, grounded):
    from insightflow_chatbot.services.insight import WITHHELD_EXPLANATION

    result = QuestionResult(metric_result=MetricResult(metric_name="m", shape="scalar", value=value, format=format_, metadata=_metadata()))
    got = InsightGenerator(make_fake_llm_client(_ExplanationOutput(explanation=explanation))).explain("q?", result)

    assert (got == explanation) is grounded
    assert grounded or got == WITHHELD_EXPLANATION


def test_a_category_delta_explanation_may_state_the_change_as_a_percentage(make_fake_llm_client):
    deltas = [CategoryDelta(dimension_value="Gadgets", current_value=100.0, comparison_value=200.0, delta=-100.0, pct_change=-0.5)]
    explanation = "Gadgets declined the most, from 200 to 100 (a 50% drop)."
    llm = make_fake_llm_client(_ExplanationOutput(explanation=explanation))

    assert InsightGenerator(llm).explain("Which category declined?", QuestionResult(category_deltas=deltas)) == explanation
