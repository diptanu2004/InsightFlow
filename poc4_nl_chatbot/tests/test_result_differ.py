from insightflow_core.models import MetricResult, QueryMetadata

from insightflow_chatbot.services.result_differ import ResultDiffer


def _grouped_result(rows: list[dict]) -> MetricResult:
    return MetricResult(metric_name="revenue", shape="grouped", rows=rows, metadata=QueryMetadata(sql="", execution_time_ms=0.0, row_count=len(rows)))


def test_diff_computes_delta_for_a_category_present_in_both_periods():
    current = _grouped_result([{"category": "Gadgets", "value": 540.0}])
    comparison = _grouped_result([{"category": "Gadgets", "value": 120.0}])
    deltas = ResultDiffer().diff(current, comparison, "category")
    assert len(deltas) == 1
    assert deltas[0].dimension_value == "Gadgets"
    assert deltas[0].current_value == 540.0
    assert deltas[0].comparison_value == 120.0
    assert deltas[0].delta == 420.0
    assert deltas[0].pct_change == 3.5


def test_diff_zero_fills_a_category_missing_from_the_current_period():
    current = _grouped_result([])
    comparison = _grouped_result([{"category": "Home", "value": 80.0}])
    deltas = ResultDiffer().diff(current, comparison, "category")
    assert len(deltas) == 1
    assert deltas[0].current_value == 0.0
    assert deltas[0].comparison_value == 80.0
    assert deltas[0].delta == -80.0


def test_diff_zero_fills_a_category_missing_from_the_comparison_period():
    current = _grouped_result([{"category": "Gadgets", "value": 100.0}])
    comparison = _grouped_result([])
    deltas = ResultDiffer().diff(current, comparison, "category")
    assert len(deltas) == 1
    assert deltas[0].comparison_value == 0.0
    assert deltas[0].delta == 100.0


def test_pct_change_is_none_not_an_error_when_comparison_is_zero():
    current = _grouped_result([{"category": "Gadgets", "value": 100.0}])
    comparison = _grouped_result([])
    deltas = ResultDiffer().diff(current, comparison, "category")
    assert deltas[0].pct_change is None


def test_diff_ranks_by_most_negative_delta_first():
    current = _grouped_result([{"category": "A", "value": 50.0}, {"category": "B", "value": 90.0}])
    comparison = _grouped_result([{"category": "A", "value": 100.0}, {"category": "B", "value": 100.0}])
    deltas = ResultDiffer().diff(current, comparison, "category")
    # A: -50 delta, B: -10 delta -- A (bigger decline) should rank first
    assert [d.dimension_value for d in deltas] == ["A", "B"]
