from insightflow_dashboard.dashboard.planner import DashboardPlanner
from insightflow_dashboard.dashboard.planner_context import MetricSummary, PlannerContext
from insightflow_dashboard.dashboard.signal import SignalResult
from insightflow_dashboard.dashboard.spec import ComponentSpec, ComponentType, DashboardSpec
from insightflow_core.models import MetricResult, QueryMetadata


def _sample_context() -> PlannerContext:
    signal = SignalResult(
        name="total_revenue",
        result=MetricResult(metric_name="revenue", shape="scalar", value=790.0, metadata=QueryMetadata(sql="...", execution_time_ms=1.0, row_count=1)),
    )
    return PlannerContext(
        entities=["orders", "customers", "products"],
        available_dimensions=["category", "region"],
        available_metrics=[
            MetricSummary(name="revenue", kind="measure", description="raw measure on orders"),
            MetricSummary(name="aov", kind="ratio", description="Average Order Value"),
            MetricSummary(name="revenue_growth", kind="growth", description="Revenue growth between two periods"),
        ],
        signals=[signal],
        supported_component_types=[ComponentType.KPI, ComponentType.BAR_CHART],
        groupable_metrics=["revenue"],
        # region is an available dimension but deliberately NOT joinable to revenue here.
        groupable_dimensions={"revenue": ["category"]},
        resolvable_metrics=["revenue", "aov"],
    )


def _fixed_spec() -> DashboardSpec:
    return DashboardSpec(
        title="Overview",
        narrative="Revenue overview",
        components=[
            ComponentSpec(component_id="kpi_revenue", type=ComponentType.KPI, metric_name="revenue"),
            ComponentSpec(component_id="c2", type=ComponentType.KPI, metric_name="orders"),
            ComponentSpec(component_id="c3", type=ComponentType.KPI, metric_name="customers"),
        ],
    )


def test_plan_delegates_to_llm_client_and_returns_its_output(make_fake_llm_client):
    fixed = _fixed_spec()
    client = make_fake_llm_client(fixed)
    planner = DashboardPlanner(client, min_components=3, max_components=8)

    result = planner.plan(_sample_context())

    assert result is fixed
    assert client.last_prompt is not None


def test_prompt_includes_metrics_dimensions_signals_and_allowed_component_types(make_fake_llm_client):
    client = make_fake_llm_client(_fixed_spec())
    planner = DashboardPlanner(client, min_components=3, max_components=8)

    planner.plan(_sample_context())
    prompt = client.last_prompt

    assert "revenue" in prompt
    assert "category" in prompt and "region" in prompt
    assert "total_revenue" in prompt
    assert "kpi" in prompt and "bar_chart" in prompt
    allowed_line = next(line for line in prompt.splitlines() if line.startswith("Allowed component types:"))
    assert "line_chart" not in allowed_line  # not in supported_component_types for this context
    assert "3" in prompt and "8" in prompt  # min/max component counts


def test_prompt_restricts_grouping_components_to_groupable_metrics_only(make_fake_llm_client):
    """Found via the real Groq/real-Olist run: the planner picked a ratio-kind metric ("aov") for
    a bar_chart, which DashboardValidator always rejects. The prompt must tell it up front which
    metrics are safe to group by."""
    client = make_fake_llm_client(_fixed_spec())
    planner = DashboardPlanner(client, min_components=3, max_components=8)

    planner.plan(_sample_context())
    prompt = client.last_prompt

    lines = prompt.splitlines()
    assert "- revenue: category" in lines
    assert not any(line.startswith("- aov:") for line in lines)


def test_prompt_pairs_each_groupable_metric_only_with_dimensions_it_can_join_to(make_fake_llm_client):
    """Phase 8 M4: listing groupable metrics and dimensions as two independent lists let the planner
    pair any of them, but the engine can only join over a single-hop relationship. On the real Olist
    upload revenue and category are two hops apart, and one such component fails the whole
    dashboard -- so the prompt must offer pairs, not a cross product."""
    client = make_fake_llm_client(_fixed_spec())
    planner = DashboardPlanner(client, min_components=3, max_components=8)

    planner.plan(_sample_context())
    prompt = client.last_prompt

    revenue_line = next(line for line in prompt.splitlines() if line.startswith("- revenue:"))
    assert "category" in revenue_line and "region" not in revenue_line
    assert "dimensions listed for THAT metric" in prompt


def test_prompt_says_kpis_only_when_no_grouping_is_possible(make_fake_llm_client):
    client = make_fake_llm_client(_fixed_spec())
    planner = DashboardPlanner(client, min_components=3, max_components=8)
    context = _sample_context()
    context.groupable_metrics = []
    context.groupable_dimensions = {}

    planner.plan(context)

    assert "use only kpi components" in client.last_prompt


def test_prompt_never_offers_a_growth_metric_as_a_usable_metric_name(make_fake_llm_client):
    """Found via a third real Groq/real-Olist run: the planner picked "revenue_growth" (kind=growth)
    for a KPI, which DashboardDataResolver cannot resolve at all (it never builds a GROWTH query) --
    DashboardValidator caught it, but only after a real LLM call. The prompt must say up front that
    growth metrics can never be a component's metric_name, in any component type."""
    client = make_fake_llm_client(_fixed_spec())
    planner = DashboardPlanner(client, min_components=3, max_components=8)

    planner.plan(_sample_context())
    prompt = client.last_prompt

    resolvable_line = next(line for line in prompt.splitlines() if line.startswith("Metrics usable as any component's"))
    assert "revenue" in resolvable_line and "aov" in resolvable_line
    assert "revenue_growth" not in resolvable_line


def test_prompt_instructs_planner_to_reconcile_conflicting_signals_in_the_narrative(make_fake_llm_client):
    """Found via the retention-problem benchmark run: the planner correctly queried
    repeat_purchase_rate as a KPI (the mechanical part of this case), but its narrative and that
    KPI's rationale described it as a neutral "loyalty" metric alongside healthy-looking growth
    signals, never calling out that ~40% growth resting on a ~5% repeat-purchase rate means growth
    is being driven almost entirely by new-customer acquisition, not real retention. Nothing in
    the prompt asked the planner to reconcile a metric that tells a different story than the
    growth signals -- this test locks in that instruction."""
    client = make_fake_llm_client(_fixed_spec())
    planner = DashboardPlanner(client, min_components=3, max_components=8)

    planner.plan(_sample_context())
    prompt = client.last_prompt

    assert "do not by themselves mean the business is healthy" in prompt
    assert "repeat-purchase rate" in prompt
    assert "name that tension explicitly" in prompt
    narrative_sentence = next(line for line in prompt.splitlines() if "`narrative` to a one- or two-sentence VERDICT" in line)
    assert "reconciling any tension" in narrative_sentence
