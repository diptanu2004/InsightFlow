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

    assert "790.0" in fake_client.last_prompt


def test_explain_prompt_includes_category_deltas(make_fake_llm_client):
    fake_client = make_fake_llm_client(_ExplanationOutput(explanation="x"))
    generator = InsightGenerator(fake_client)
    deltas = [CategoryDelta(dimension_value="Gadgets", current_value=100.0, comparison_value=200.0, delta=-100.0, pct_change=-0.5)]
    result = QuestionResult(category_deltas=deltas)

    generator.explain("Which category declined?", result)

    assert "Gadgets" in fake_client.last_prompt
    assert "-100.00" in fake_client.last_prompt
