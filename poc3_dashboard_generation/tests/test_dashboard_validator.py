from insightflow.dashboard.spec import ComponentSpec, ComponentType, DashboardSpec
from insightflow.dashboard.validator import DashboardValidator


def _validator(registry, field_resolver, min_c=3, max_c=8):
    return DashboardValidator(registry, field_resolver, min_c, max_c)


def test_valid_spec_passes(registry, field_resolver):
    v = _validator(registry, field_resolver)
    spec = DashboardSpec(
        title="Overview",
        components=[
            ComponentSpec(component_id="c1", type=ComponentType.KPI, metric_name="revenue"),
            ComponentSpec(component_id="c2", type=ComponentType.KPI, metric_name="orders"),
            ComponentSpec(component_id="c3", type=ComponentType.BAR_CHART, metric_name="revenue", dimension="category"),
        ],
    )
    result = v.validate(spec)
    assert result.is_valid
    assert result.errors == []


def test_unknown_metric_is_rejected_with_component_id(registry, field_resolver):
    v = _validator(registry, field_resolver)
    spec = DashboardSpec(
        title="Overview",
        components=[
            ComponentSpec(component_id="c1", type=ComponentType.KPI, metric_name="does_not_exist"),
            ComponentSpec(component_id="c2", type=ComponentType.KPI, metric_name="orders"),
            ComponentSpec(component_id="c3", type=ComponentType.KPI, metric_name="customers"),
        ],
    )
    result = v.validate(spec)
    assert not result.is_valid
    errs = {(e.code, e.component_id) for e in result.errors}
    assert ("unknown_metric", "c1") in errs


def test_unknown_dimension_is_rejected(registry, field_resolver):
    v = _validator(registry, field_resolver)
    spec = DashboardSpec(
        title="Overview",
        components=[
            ComponentSpec(component_id="c1", type=ComponentType.BAR_CHART, metric_name="revenue", dimension="not_a_field"),
            ComponentSpec(component_id="c2", type=ComponentType.KPI, metric_name="orders"),
            ComponentSpec(component_id="c3", type=ComponentType.KPI, metric_name="customers"),
        ],
    )
    result = v.validate(spec)
    assert not result.is_valid
    assert any(e.code == "unknown_dimension" and e.component_id == "c1" for e in result.errors)


def test_group_by_rejected_for_non_base_metric_kind(registry, field_resolver):
    """Real POC 2 closure bug (finding 9): GROUP_BY against a RATIO metric like `aov` used to
    silently return the wrong shape. DashboardValidator must reject this at spec time."""
    v = _validator(registry, field_resolver)
    spec = DashboardSpec(
        title="Overview",
        components=[
            ComponentSpec(component_id="c1", type=ComponentType.KPI, metric_name="revenue"),
            ComponentSpec(component_id="c2", type=ComponentType.KPI, metric_name="orders"),
            ComponentSpec(component_id="c3", type=ComponentType.BAR_CHART, metric_name="aov", dimension="category"),
        ],
    )
    result = v.validate(spec)
    assert not result.is_valid
    assert any(e.code == "group_by_unsupported_for_metric_kind" and e.component_id == "c3" for e in result.errors)


def test_growth_metric_is_rejected_as_not_directly_resolvable_even_as_a_kpi(registry, field_resolver):
    """Found via a real Groq/real-Olist run: DashboardDataResolver.build_query() never builds a
    GROWTH query (it needs an explicit comparison-period pair nothing in ComponentSpec supplies),
    so a growth-kind metric like `revenue_growth` picked for a KPI used to sail through validation
    (only grouping components were checked for metric-kind restrictions) and crash deep inside
    SQLCompiler._compile_growth instead of being rejected here."""
    v = _validator(registry, field_resolver)
    spec = DashboardSpec(
        title="Overview",
        components=[
            ComponentSpec(component_id="c1", type=ComponentType.KPI, metric_name="revenue"),
            ComponentSpec(component_id="c2", type=ComponentType.KPI, metric_name="revenue_growth"),
            ComponentSpec(component_id="c3", type=ComponentType.KPI, metric_name="orders"),
        ],
    )
    result = v.validate(spec)
    assert not result.is_valid
    assert any(e.code == "growth_metric_not_directly_resolvable" and e.component_id == "c2" for e in result.errors)


def test_line_chart_is_rejected_as_not_yet_supported(registry, field_resolver):
    v = _validator(registry, field_resolver)
    spec = DashboardSpec(
        title="Overview",
        components=[
            ComponentSpec(component_id="c1", type=ComponentType.KPI, metric_name="revenue"),
            ComponentSpec(component_id="c2", type=ComponentType.KPI, metric_name="orders"),
            ComponentSpec(
                component_id="c3",
                type=ComponentType.LINE_CHART,
                metric_name="revenue",
                dimension="transaction_date",
                time_grain="month",
            ),
        ],
    )
    result = v.validate(spec)
    assert not result.is_valid
    assert any(e.code == "component_type_not_yet_supported" and e.component_id == "c3" for e in result.errors)


def test_duplicate_kpi_is_rejected(registry, field_resolver):
    v = _validator(registry, field_resolver)
    spec = DashboardSpec(
        title="Overview",
        components=[
            ComponentSpec(component_id="c1", type=ComponentType.KPI, metric_name="revenue"),
            ComponentSpec(component_id="c2", type=ComponentType.KPI, metric_name="revenue"),
            ComponentSpec(component_id="c3", type=ComponentType.KPI, metric_name="orders"),
        ],
    )
    result = v.validate(spec)
    assert not result.is_valid
    assert any(e.code == "duplicate_kpi" for e in result.errors)


def test_too_few_components_is_rejected(registry, field_resolver):
    v = _validator(registry, field_resolver, min_c=3, max_c=8)
    spec = DashboardSpec(
        title="Overview",
        components=[ComponentSpec(component_id="c1", type=ComponentType.KPI, metric_name="revenue")],
    )
    result = v.validate(spec)
    assert not result.is_valid
    assert any(e.code == "too_few_components" for e in result.errors)


def test_too_many_components_is_rejected(registry, field_resolver):
    v = _validator(registry, field_resolver, min_c=1, max_c=2)
    spec = DashboardSpec(
        title="Overview",
        components=[
            ComponentSpec(component_id="c1", type=ComponentType.KPI, metric_name="revenue"),
            ComponentSpec(component_id="c2", type=ComponentType.KPI, metric_name="orders"),
            ComponentSpec(component_id="c3", type=ComponentType.KPI, metric_name="customers"),
        ],
    )
    result = v.validate(spec)
    assert not result.is_valid
    assert any(e.code == "too_many_components" for e in result.errors)


def test_unknown_filter_dimension_is_rejected(registry, field_resolver):
    from insightflow.dashboard.spec import FilterSpec

    v = _validator(registry, field_resolver, min_c=1, max_c=8)
    spec = DashboardSpec(
        title="Overview",
        filters=[FilterSpec(dimension="not_a_field", label="Not a field")],
        components=[ComponentSpec(component_id="c1", type=ComponentType.KPI, metric_name="revenue")],
    )
    result = v.validate(spec)
    assert not result.is_valid
    assert any(e.code == "unknown_filter_dimension" for e in result.errors)
