from datetime import date

import pytest

from insightflow_dashboard.dashboard.pipeline import DashboardGenerationPipeline
from insightflow_dashboard.dashboard.resolver import DashboardDataResolver
from insightflow_dashboard.dashboard.signal import SignalGatherer
from insightflow_dashboard.dashboard.spec import ComponentSpec, ComponentType, DashboardSpec
from insightflow_dashboard.dashboard.validator import DashboardValidator
from insightflow_dashboard.dashboard.planner import DashboardPlanner


def _build_pipeline(engine, semantic_model, registry, field_resolver, fake_output, make_fake_llm_client):
    return DashboardGenerationPipeline(
        semantic_model=semantic_model,
        registry=registry,
        signal_gatherer=SignalGatherer(engine, semantic_model, registry, reference_date=date(2026, 3, 1)),
        planner=DashboardPlanner(make_fake_llm_client(fake_output), min_components=3, max_components=8),
        validator=DashboardValidator(registry, field_resolver, min_components=3, max_components=8),
        resolver=DashboardDataResolver(engine),
        field_resolver=field_resolver,
    )


def test_full_pipeline_end_to_end_with_a_valid_planner_output(
    engine, semantic_model, registry, field_resolver, make_fake_llm_client
):
    fake_spec = DashboardSpec(
        title="Executive Overview",
        narrative="Revenue is healthy; Gadgets leads.",
        components=[
            ComponentSpec(component_id="kpi_revenue", type=ComponentType.KPI, metric_name="revenue"),
            ComponentSpec(component_id="kpi_orders", type=ComponentType.KPI, metric_name="orders"),
            ComponentSpec(component_id="kpi_aov", type=ComponentType.KPI, metric_name="aov"),
            ComponentSpec(component_id="bar_category", type=ComponentType.BAR_CHART, metric_name="revenue", dimension="category"),
        ],
    )
    pipeline = _build_pipeline(engine, semantic_model, registry, field_resolver, fake_spec, make_fake_llm_client)

    dashboard = pipeline.run()

    assert dashboard.title == "Executive Overview"
    assert len(dashboard.components) == 4
    kpi_revenue = next(c for c in dashboard.components if c.spec.component_id == "kpi_revenue")
    assert kpi_revenue.result.value == 790.0


def test_full_pipeline_rejects_an_invalid_planner_output_before_touching_the_engine(
    engine, semantic_model, registry, field_resolver, make_fake_llm_client
):
    invalid_spec = DashboardSpec(
        title="Broken",
        components=[
            ComponentSpec(component_id="c1", type=ComponentType.KPI, metric_name="revenue"),
            ComponentSpec(component_id="c2", type=ComponentType.KPI, metric_name="orders"),
            ComponentSpec(component_id="c3", type=ComponentType.BAR_CHART, metric_name="aov", dimension="category"),
        ],
    )
    pipeline = _build_pipeline(engine, semantic_model, registry, field_resolver, invalid_spec, make_fake_llm_client)

    with pytest.raises(ValueError, match="invalid dashboard spec"):
        pipeline.run()


def test_planner_context_only_exposes_currently_supported_component_types(
    engine, semantic_model, registry, field_resolver, make_fake_llm_client
):
    """The planner must never be told it can use line_chart -- it isn't resolvable yet
    (docs/class_diagram.md's banner note)."""
    fake_spec = DashboardSpec(
        title="Overview",
        components=[
            ComponentSpec(component_id="c1", type=ComponentType.KPI, metric_name="revenue"),
            ComponentSpec(component_id="c2", type=ComponentType.KPI, metric_name="orders"),
            ComponentSpec(component_id="c3", type=ComponentType.KPI, metric_name="customers"),
        ],
    )
    client = make_fake_llm_client(fake_spec)
    pipeline = DashboardGenerationPipeline(
        semantic_model=semantic_model,
        registry=registry,
        signal_gatherer=SignalGatherer(engine, semantic_model, registry, reference_date=date(2026, 3, 1)),
        planner=DashboardPlanner(client, min_components=3, max_components=8),
        validator=DashboardValidator(registry, field_resolver, min_components=3, max_components=8),
        resolver=DashboardDataResolver(engine),
        field_resolver=field_resolver,
    )
    pipeline.run()
    allowed_line = next(line for line in client.last_prompt.splitlines() if line.startswith("Allowed component types:"))
    assert "line_chart" not in allowed_line


def test_planner_context_never_offers_a_dimension_that_the_validator_would_reject_as_ambiguous(
    engine, semantic_model, registry, field_resolver, make_fake_llm_client
):
    """Found via the real Groq/real-Olist run (examples/real_olist_integration): the planner used
    to be told about every canonical field name as a usable dimension, ambiguous ones included, so
    it happily proposed a GROUP_BY on one -- which DashboardValidator then (correctly) rejected.
    This POC's own sample semantic model already has one such case: "customer_id" is a canonical
    field in BOTH the "orders" and "customers" entities, so it must never appear in the planner's
    "Available dimensions" prompt line even though it's a real field in the model."""
    fake_spec = DashboardSpec(
        title="Overview",
        components=[
            ComponentSpec(component_id="c1", type=ComponentType.KPI, metric_name="revenue"),
            ComponentSpec(component_id="c2", type=ComponentType.KPI, metric_name="orders"),
            ComponentSpec(component_id="c3", type=ComponentType.KPI, metric_name="customers"),
        ],
    )
    pipeline = _build_pipeline(engine, semantic_model, registry, field_resolver, fake_spec, make_fake_llm_client)

    pipeline.run()

    client = pipeline.planner.llm_client
    dimensions_line = next(line for line in client.last_prompt.splitlines() if line.startswith("Available dimensions"))
    assert "customer_id" not in dimensions_line
    assert "category" in dimensions_line  # unambiguous -- must still be offered


def test_planner_context_never_offers_a_ratio_metric_as_groupable(
    engine, semantic_model, registry, field_resolver, make_fake_llm_client
):
    """Found via the real Groq/real-Olist run: the planner picked "aov" (kind=ratio) for a
    bar_chart, which DashboardValidator always rejects (RATIO/GROWTH/HAVING_RATIO grouping isn't
    implemented in POC 2). The sample dataset's own registry already has this case -- "aov" is a
    RATIO metric registered alongside the groupable "revenue"/"orders"/"customers" measures."""
    fake_spec = DashboardSpec(
        title="Overview",
        components=[
            ComponentSpec(component_id="c1", type=ComponentType.KPI, metric_name="revenue"),
            ComponentSpec(component_id="c2", type=ComponentType.KPI, metric_name="orders"),
            ComponentSpec(component_id="c3", type=ComponentType.KPI, metric_name="customers"),
        ],
    )
    pipeline = _build_pipeline(engine, semantic_model, registry, field_resolver, fake_spec, make_fake_llm_client)

    pipeline.run()

    client = pipeline.planner.llm_client
    # Groupable metrics are listed one per line with their joinable dimensions ("- revenue: ...").
    grouping_lines = [line for line in client.last_prompt.splitlines() if line.startswith("- ") and ": " in line]
    assert any(line.startswith("- revenue: ") for line in grouping_lines)
    assert not any(line.startswith("- aov: ") for line in grouping_lines)


def test_planner_context_never_offers_a_growth_metric_as_a_usable_metric_name(
    engine, semantic_model, registry, field_resolver, make_fake_llm_client
):
    """Found via a third real Groq/real-Olist run: the planner picked "revenue_growth" (kind=growth)
    for a KPI, which DashboardDataResolver has no way to resolve at all (it never builds a GROWTH
    query). Confirms the fix end to end through the real pipeline wiring, not just the planner
    prompt-building unit tests in test_dashboard_planner.py."""
    fake_spec = DashboardSpec(
        title="Overview",
        components=[
            ComponentSpec(component_id="c1", type=ComponentType.KPI, metric_name="revenue"),
            ComponentSpec(component_id="c2", type=ComponentType.KPI, metric_name="orders"),
            ComponentSpec(component_id="c3", type=ComponentType.KPI, metric_name="customers"),
        ],
    )
    pipeline = _build_pipeline(engine, semantic_model, registry, field_resolver, fake_spec, make_fake_llm_client)

    pipeline.run()

    client = pipeline.planner.llm_client
    resolvable_line = next(line for line in client.last_prompt.splitlines() if line.startswith("Metrics usable as any component's"))
    assert "revenue_growth" not in resolvable_line
    assert "revenue" in resolvable_line and "aov" in resolvable_line  # aov is resolvable, just not groupable
