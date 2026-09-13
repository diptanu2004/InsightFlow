"""End-to-end tests against the real sample dataset in data/ — these exercise every stage
(ASTValidator -> SQLCompiler -> SQLSafetyChecker -> QueryExecutor via real DuckDB), unlike
test_query_models.py which only tests the pydantic model layer.

Expected values are hand-computed from data/raw/sample/*.csv — see the fixture JSON files in
data/fixtures/ for the same numbers, computed the same way.
"""
import json
from pathlib import Path

import pytest

from insightflow_core.models import (
    AnalyticalQuery,
    GrowthSpec,
    HavingClause,
    OperationType,
    SemanticModel,
    SortSpec,
    TimeFilter,
)
from insightflow_core.pipeline import build_pipeline
from insightflow_analytics.registry import bootstrap_registry

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture()
def pipeline():
    semantic_model = SemanticModel(**json.loads((DATA_DIR / "semantic_model.json").read_text()))
    registry = bootstrap_registry()
    return build_pipeline(semantic_model, registry, str(DATA_DIR / "raw" / "sample"))


def test_revenue(pipeline):
    result = pipeline.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="revenue"))
    assert result.value == pytest.approx(790.0)


def test_orders(pipeline):
    result = pipeline.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="orders"))
    assert result.value == pytest.approx(7.0)


def test_customers(pipeline):
    result = pipeline.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="customers"))
    assert result.value == pytest.approx(4.0)


def test_aov_ratio(pipeline):
    result = pipeline.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="aov"))
    assert result.value == pytest.approx(790.0 / 7.0)


def test_repeat_purchase_rate_having_ratio(pipeline):
    result = pipeline.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="repeat_purchase_rate"))
    assert result.value == pytest.approx(0.5)


def test_repeat_purchase_rate_having_override(pipeline):
    # override the default ">=2" threshold to ">=3" -> only customer C1 (3 orders) qualifies
    override = HavingClause(field="orders_per_customer", operator=">=", value=3)
    query = AnalyticalQuery(operation=OperationType.AGGREGATE, metric="repeat_purchase_rate", having_override=override)
    result = pipeline.run(query)
    assert result.value == pytest.approx(0.25)


def test_revenue_growth(pipeline):
    growth = GrowthSpec(
        current_period=TimeFilter(start_date="2026-01-01", end_date="2026-02-28"),
        comparison_period=TimeFilter(start_date="2025-10-01", end_date="2025-12-31"),
    )
    result = pipeline.run(AnalyticalQuery(operation=OperationType.GROWTH, metric="revenue_growth", growth=growth))
    assert result.value == pytest.approx(1.95)


def test_category_performance_group_by(pipeline):
    query = AnalyticalQuery(
        operation=OperationType.GROUP_BY,
        metric="revenue",
        dimension="category",
        sort=SortSpec(field="category", direction="asc"),
    )
    result = pipeline.run(query)
    assert result.rows == [
        {"category": "Gadgets", "value": 660.0},
        {"category": "Home", "value": 130.0},
    ]


def test_regional_performance_group_by_across_join(pipeline):
    # "region" lives on the customers entity, revenue's measure lives on orders -> exercises
    # the single-hop join in FieldResolver.resolve_join_path / SQLCompiler._apply_join.
    query = AnalyticalQuery(
        operation=OperationType.GROUP_BY,
        metric="revenue",
        dimension="region",
        sort=SortSpec(field="region", direction="asc"),
    )
    result = pipeline.run(query)
    assert result.rows == [
        {"region": "North", "value": 380.0},
        {"region": "South", "value": 320.0},
        {"region": "West", "value": 90.0},
    ]


def test_pipeline_rejects_invalid_query(pipeline):
    with pytest.raises(ValueError, match="invalid query"):
        pipeline.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="not_a_real_metric"))


def test_pipeline_rejects_row_limit_over_max(pipeline):
    with pytest.raises(ValueError, match="invalid query"):
        pipeline.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="revenue", limit=999_999))


def test_sum_measure_with_no_matching_rows_returns_zero_not_a_crash(pipeline):
    """Found during the POC 2 closure sweep: SUM() over zero matching rows is SQL NULL, which
    used to crash the whole query with an unhandled pydantic ValidationError (MetricResult
    requires exactly one of value/rows, and a NULL scalar left neither set). A period with no
    revenue is a routine query, not an edge case."""
    out_of_range = TimeFilter(start_date="2099-01-01", end_date="2099-12-31")
    result = pipeline.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="revenue", time_filter=out_of_range))
    assert result.value == pytest.approx(0.0)


def test_having_ratio_with_time_filter_is_rejected_not_silently_wrong(pipeline):
    """_compile_having_ratio never applied query.time_filter at all -- a time-filtered
    repeat_purchase_rate used to compile fine and silently return the UNFILTERED whole-dataset
    answer. Must be a clean rejection, not a wrong number."""
    out_of_range = TimeFilter(start_date="2099-01-01", end_date="2099-12-31")
    with pytest.raises(ValueError, match="invalid query"):
        pipeline.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="repeat_purchase_rate", time_filter=out_of_range))
