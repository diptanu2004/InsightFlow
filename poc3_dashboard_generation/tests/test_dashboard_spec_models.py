import pytest
from pydantic import ValidationError

from insightflow.dashboard.spec import ComponentSpec, ComponentType, DashboardSpec, FilterSpec


def test_kpi_component_needs_no_dimension():
    c = ComponentSpec(component_id="c1", type=ComponentType.KPI, metric_name="revenue")
    assert c.dimension is None


def test_kpi_component_rejects_a_dimension():
    with pytest.raises(ValidationError):
        ComponentSpec(component_id="c1", type=ComponentType.KPI, metric_name="revenue", dimension="category")


@pytest.mark.parametrize("component_type", [ComponentType.BAR_CHART, ComponentType.PIE_CHART, ComponentType.TABLE])
def test_grouping_component_requires_a_dimension(component_type):
    with pytest.raises(ValidationError):
        ComponentSpec(component_id="c1", type=component_type, metric_name="revenue")


@pytest.mark.parametrize("component_type", [ComponentType.BAR_CHART, ComponentType.PIE_CHART, ComponentType.TABLE])
def test_grouping_component_accepts_a_dimension(component_type):
    c = ComponentSpec(component_id="c1", type=component_type, metric_name="revenue", dimension="category")
    assert c.dimension == "category"


def test_time_grain_only_valid_on_line_chart():
    with pytest.raises(ValidationError):
        ComponentSpec(
            component_id="c1", type=ComponentType.BAR_CHART, metric_name="revenue", dimension="category", time_grain="month"
        )


def test_time_grain_valid_on_line_chart():
    c = ComponentSpec(
        component_id="c1", type=ComponentType.LINE_CHART, metric_name="revenue", dimension="transaction_date", time_grain="month"
    )
    assert c.time_grain == "month"


def test_dashboard_spec_defaults_to_empty_filters_and_components():
    spec = DashboardSpec(title="Overview")
    assert spec.filters == []
    assert spec.components == []


def test_filter_spec_requires_dimension_and_label():
    with pytest.raises(ValidationError):
        FilterSpec(dimension="region")
