from datetime import date

import pytest
from pydantic import ValidationError as PydanticValidationError

from insightflow_core.models import (
    AggregationType,
    AnalyticalQuery,
    GrowthSpec,
    HavingClause,
    Measure,
    MetricDefinition,
    MetricKind,
    MetricResult,
    OperationType,
    QueryMetadata,
    TimeFilter,
)


def test_aggregate_query_constructs():
    q = AnalyticalQuery(operation=OperationType.AGGREGATE, metric="revenue")
    assert q.dimension is None
    assert q.growth is None


def test_group_by_requires_no_growth_field():
    q = AnalyticalQuery(operation=OperationType.GROUP_BY, metric="revenue", dimension="category")
    assert q.dimension == "category"


def test_dimension_rejected_outside_group_by():
    with pytest.raises(PydanticValidationError):
        AnalyticalQuery(operation=OperationType.AGGREGATE, metric="revenue", dimension="category")


def test_growth_query_requires_growth_field():
    with pytest.raises(PydanticValidationError):
        AnalyticalQuery(operation=OperationType.GROWTH, metric="revenue")


def test_growth_query_rejects_time_filter():
    growth = GrowthSpec(
        current_period=TimeFilter(start_date=date(2026, 1, 1), end_date=date(2026, 3, 31)),
        comparison_period=TimeFilter(start_date=date(2025, 10, 1), end_date=date(2025, 12, 31)),
    )
    with pytest.raises(PydanticValidationError):
        AnalyticalQuery(
            operation=OperationType.GROWTH,
            metric="revenue",
            growth=growth,
            time_filter=growth.current_period,
        )


def test_time_filter_rejects_end_before_start():
    with pytest.raises(PydanticValidationError):
        TimeFilter(start_date=date(2026, 3, 31), end_date=date(2026, 1, 1))


def test_having_clause_rejects_unknown_operator():
    with pytest.raises(PydanticValidationError):
        HavingClause(field="order_count", operator="~=", value=2)


def test_ratio_metric_requires_numerator_and_denominator():
    with pytest.raises(PydanticValidationError):
        MetricDefinition(name="aov", kind=MetricKind.RATIO, numerator_measure="revenue")


def test_having_ratio_metric_requires_default_having():
    with pytest.raises(PydanticValidationError):
        MetricDefinition(
            name="repeat_purchase_rate",
            kind=MetricKind.HAVING_RATIO,
            numerator_measure="customers_with_2plus_orders",
            denominator_measure="customers",
        )


def test_aov_metric_definition_is_valid():
    metric = MetricDefinition(
        name="aov",
        kind=MetricKind.RATIO,
        numerator_measure="revenue",
        denominator_measure="orders",
        description="Average Order Value",
    )
    assert metric.numerator_measure == "revenue"


def test_measure_carries_default_aggregation():
    revenue = Measure(name="revenue", entity="orders", source_field="revenue", aggregation=AggregationType.SUM)
    assert revenue.aggregation == AggregationType.SUM


def test_metric_result_rejects_both_value_and_rows():
    metadata = QueryMetadata(sql="SELECT 1", execution_time_ms=1.0, row_count=1)
    with pytest.raises(PydanticValidationError):
        MetricResult(metric_name="revenue", value=100.0, rows=[{"category": "a"}], metadata=metadata)


def test_metric_result_rejects_neither_value_nor_rows():
    metadata = QueryMetadata(sql="SELECT 1", execution_time_ms=1.0, row_count=1)
    with pytest.raises(PydanticValidationError):
        MetricResult(metric_name="revenue", metadata=metadata)


def test_metric_result_scalar_shape():
    metadata = QueryMetadata(sql="SELECT SUM(revenue) FROM orders", execution_time_ms=2.1, row_count=1)
    result = MetricResult(metric_name="revenue", value=12345.0, metadata=metadata)
    assert result.value == 12345.0
    assert result.rows is None
