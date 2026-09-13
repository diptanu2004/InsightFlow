from insightflow_core.compilation import FieldResolver

from insightflow.models.intent import QuestionIntent, QuestionOperation
from insightflow.services.planner import QuestionPlanner
from insightflow.services.planner_context import build_planner_context


def test_planner_returns_the_fake_llms_structured_intent(semantic_model, registry, make_fake_llm_client):
    fixed_intent = QuestionIntent(answerable=True, metric_name="revenue", operation=QuestionOperation.AGGREGATE)
    planner = QuestionPlanner(make_fake_llm_client(fixed_intent))
    context = build_planner_context(semantic_model, registry, FieldResolver(semantic_model))

    intent = planner.plan("What is our total revenue?", context)

    assert intent == fixed_intent


def test_planner_prompt_lists_available_metrics_and_dimensions(semantic_model, registry, make_fake_llm_client):
    fixed_intent = QuestionIntent(answerable=False, reason="test")
    fake_client = make_fake_llm_client(fixed_intent)
    planner = QuestionPlanner(fake_client)
    context = build_planner_context(semantic_model, registry, FieldResolver(semantic_model))

    planner.plan("What is our conversion rate?", context)

    assert "revenue" in fake_client.last_prompt
    assert "category" in fake_client.last_prompt
    assert "What is our conversion rate?" in fake_client.last_prompt


def test_planner_prompt_never_offers_a_growth_kind_metric_as_groupable(semantic_model, registry, make_fake_llm_client):
    fixed_intent = QuestionIntent(answerable=False, reason="test")
    fake_client = make_fake_llm_client(fixed_intent)
    planner = QuestionPlanner(fake_client)
    context = build_planner_context(semantic_model, registry, FieldResolver(semantic_model))

    planner.plan("some question", context)

    groupable_line = next(line for line in fake_client.last_prompt.splitlines() if line.startswith("Metrics usable for operation=group_by"))
    assert "revenue_growth" not in groupable_line


def test_planner_prompt_clarifies_aggregate_allows_any_metric(semantic_model, registry, make_fake_llm_client):
    # Real finding from an M4 real-Groq run: without this sentence, a real planner once refused
    # "What is our repeat purchase rate?" (a HAVING_RATIO metric, valid for plain aggregate)
    # reasoning "repeat_purchase_rate metric is not available for the allowed operations" --
    # misreading the group_by/growth restrictions as covering every operation, not just those
    # two. Locks in the explicit carve-out for operation=aggregate.
    fixed_intent = QuestionIntent(answerable=False, reason="test")
    fake_client = make_fake_llm_client(fixed_intent)
    planner = QuestionPlanner(fake_client)
    context = build_planner_context(semantic_model, registry, FieldResolver(semantic_model))

    planner.plan("some question", context)

    assert "valid for plain operation=aggregate" in fake_client.last_prompt
