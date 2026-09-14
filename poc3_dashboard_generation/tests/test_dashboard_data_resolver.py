from insightflow_dashboard.dashboard.resolver import DashboardDataResolver
from insightflow_dashboard.dashboard.spec import ComponentSpec, ComponentType, DashboardSpec
from insightflow_core.models import OperationType


def test_build_query_for_kpi_is_a_plain_aggregate(engine):
    resolver = DashboardDataResolver(engine)
    query = resolver.build_query(ComponentSpec(component_id="c1", type=ComponentType.KPI, metric_name="revenue"))
    assert query.operation == OperationType.AGGREGATE
    assert query.dimension is None


def test_build_query_for_bar_chart_is_group_by_sorted_desc(engine):
    resolver = DashboardDataResolver(engine)
    query = resolver.build_query(
        ComponentSpec(component_id="c1", type=ComponentType.BAR_CHART, metric_name="revenue", dimension="category")
    )
    assert query.operation == OperationType.GROUP_BY
    assert query.dimension == "category"
    assert query.sort.field == "value"
    assert query.sort.direction == "desc"
    assert query.limit == 20


def test_resolve_produces_hydrated_dashboard_with_real_values(engine):
    resolver = DashboardDataResolver(engine)
    spec = DashboardSpec(
        title="Overview",
        narrative="Revenue overview",
        components=[
            ComponentSpec(component_id="kpi_revenue", type=ComponentType.KPI, metric_name="revenue"),
            ComponentSpec(component_id="bar_category", type=ComponentType.BAR_CHART, metric_name="revenue", dimension="category"),
        ],
    )
    dashboard = resolver.resolve(spec)
    assert dashboard.title == "Overview"
    assert len(dashboard.components) == 2

    kpi = next(c for c in dashboard.components if c.spec.component_id == "kpi_revenue")
    assert kpi.result.value == 790.0

    chart = next(c for c in dashboard.components if c.spec.component_id == "bar_category")
    assert chart.result.rows == [{"category": "Gadgets", "value": 660.0}, {"category": "Home", "value": 130.0}]


def test_build_query_for_pie_chart_fetches_every_group(engine):
    """A pie's percentages are shares of the whole. Capping it at the top 20 turned real Olist's
    'SP 42.5%' into a share of only the top 20 states and silently dropped the rest."""
    resolver = DashboardDataResolver(engine)
    query = resolver.build_query(
        ComponentSpec(component_id="p1", type=ComponentType.PIE_CHART, metric_name="revenue", dimension="category")
    )
    assert query.limit is None
