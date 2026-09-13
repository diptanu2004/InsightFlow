from datetime import date

import pytest

from insightflow.models.intent import QuestionIntent, QuestionOperation
from insightflow.models.time_expression import TimeExpression
from insightflow.services.executor import QuestionExecutor
from insightflow.services.query_assembler import QueryAssembler
from insightflow.services.result_differ import ResultDiffer
from insightflow.services.time_resolver import TimeExpressionResolver

# Sample dataset's real date range: 2025-11-01 (O5) to 2026-02-15 (O4) -- see data/raw/sample/orders.csv.
REFERENCE_DATE = date(2026, 2, 15)
MIN_DATE = date(2025, 11, 1)


@pytest.fixture
def assembler():
    return QueryAssembler(TimeExpressionResolver(reference_date=REFERENCE_DATE, min_date=MIN_DATE))


@pytest.fixture
def executor(engine):
    return QuestionExecutor(engine, ResultDiffer())


def test_aggregate_all_time_matches_hand_computed_total_revenue(assembler, executor):
    intent = QuestionIntent(
        answerable=True, metric_name="revenue", operation=QuestionOperation.AGGREGATE, time_expression=TimeExpression.ALL_TIME
    )
    resolved = assembler.assemble(intent)
    result = executor.execute(resolved)
    # 100+150+200+50+80+120+90 = 790, all seven sample orders
    assert result.metric_result.value == 790.0


def test_group_by_category_this_quarter_matches_hand_computed_totals(assembler, executor):
    # this_quarter for 2026-02-15 = Q1 2026 = Jan 1 - Feb 15, 2026: O1,O2,O3,O4,O7 (O5,O6 are Nov 2025)
    intent = QuestionIntent(
        answerable=True,
        metric_name="revenue",
        operation=QuestionOperation.GROUP_BY,
        dimension="category",
        time_expression=TimeExpression.THIS_QUARTER,
    )
    resolved = assembler.assemble(intent)
    result = executor.execute(resolved)
    by_category = {row["category"]: row["value"] for row in result.metric_result.rows}
    # Gadgets (P1, P2): O1 100 + O2 150 + O3 200 + O7 90 = 540; Home (P3): O4 50
    assert by_category == {"Gadgets": 540.0, "Home": 50.0}


def test_growth_by_dimension_surfaces_the_declining_category(assembler, executor):
    # last_quarter for 2026-02-15 = Q4 2025 = Oct 1 - Dec 31, 2025: O5 (Home, 80), O6 (Gadgets, 120)
    # comparison: preceding equal-length (92 days) period, Jul 1 - Sep 30, 2025: no orders at all
    intent = QuestionIntent(
        answerable=True,
        metric_name="revenue",
        operation=QuestionOperation.GROWTH_BY_DIMENSION,
        dimension="category",
        time_expression=TimeExpression.LAST_QUARTER,
    )
    resolved = assembler.assemble(intent)
    result = executor.execute(resolved)
    deltas = {d.dimension_value: d for d in result.category_deltas}
    assert deltas["Gadgets"].current_value == 120.0
    assert deltas["Gadgets"].comparison_value == 0.0
    assert deltas["Home"].current_value == 80.0
    assert deltas["Home"].comparison_value == 0.0
    # both grew from a zero base (comparison period has no orders at all) -- pct_change undefined
    assert deltas["Gadgets"].pct_change is None
    assert deltas["Home"].pct_change is None
