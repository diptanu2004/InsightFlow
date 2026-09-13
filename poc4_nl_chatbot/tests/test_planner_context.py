from insightflow_core.compilation import FieldResolver

from insightflow_chatbot.services.planner_context import build_planner_context


def test_context_lists_all_entities(semantic_model, registry):
    context = build_planner_context(semantic_model, registry, FieldResolver(semantic_model))
    assert set(context.entities) == {"orders", "customers", "products"}


def test_context_includes_unambiguous_dimensions(semantic_model, registry):
    context = build_planner_context(semantic_model, registry, FieldResolver(semantic_model))
    assert "category" in context.available_dimensions
    assert "region" in context.available_dimensions


def test_groupable_metrics_excludes_ratio_and_growth_and_having_ratio(semantic_model, registry):
    context = build_planner_context(semantic_model, registry, FieldResolver(semantic_model))
    assert "revenue" in context.groupable_metrics
    assert "aov" not in context.groupable_metrics
    assert "revenue_growth" not in context.groupable_metrics
    assert "repeat_purchase_rate" not in context.groupable_metrics


def test_growth_metrics_only_includes_growth_kind(semantic_model, registry):
    context = build_planner_context(semantic_model, registry, FieldResolver(semantic_model))
    assert set(context.growth_metrics) == {"revenue_growth", "order_growth", "customer_growth"}


def test_supported_time_expressions_lists_all_seven_values(semantic_model, registry):
    context = build_planner_context(semantic_model, registry, FieldResolver(semantic_model))
    assert len(context.supported_time_expressions) == 7


def test_context_offers_nothing_the_dataset_cannot_compute(semantic_model, registry):
    """Found in Phase 8 M4: POC 1 names entities after source filenames, so a real upload such as
    the Kaggle Olist files has no `orders` entity and every registered measure is uncomputable.
    Offering those to the planner let it plan queries that crashed inside the engine."""
    for entity in semantic_model.entities:
        if entity.name == "orders":
            entity.name = "olist_orders_dataset"

    context = build_planner_context(semantic_model, registry, FieldResolver(semantic_model))

    assert context.available_metrics == []
    assert context.groupable_metrics == []
    assert context.growth_metrics == []


def test_growth_metrics_withheld_when_the_measure_entity_has_no_time_field(semantic_model, registry):
    for entity in semantic_model.entities:
        if entity.name == "orders":
            entity.fields = [f for f in entity.fields if f.name != "transaction_date"]

    context = build_planner_context(semantic_model, registry, FieldResolver(semantic_model))

    assert context.growth_metrics == []
    assert "revenue" in context.groupable_metrics
