from insightflow_core.compilation import FieldResolver

from insightflow.services.planner_context import build_planner_context


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
