from datetime import date

import pytest

from insightflow_chatbot.models.time_expression import TimeExpression
from insightflow_chatbot.services.time_resolver import TimeExpressionResolver


@pytest.fixture
def resolver():
    # 2024-08-15 is a Thursday in Q3; deliberately not the 1st/last of any period, so a bug that
    # accidentally reads month/quarter/year boundaries off the wrong end would still fail loudly.
    return TimeExpressionResolver(reference_date=date(2024, 8, 15), min_date=date(2022, 1, 1))


def test_this_month_starts_at_first_of_month_and_ends_at_reference_date(resolver):
    tf = resolver.resolve_time_filter(TimeExpression.THIS_MONTH)
    assert tf.start_date == date(2024, 8, 1)
    assert tf.end_date == date(2024, 8, 15)


def test_last_month_is_the_full_preceding_calendar_month(resolver):
    tf = resolver.resolve_time_filter(TimeExpression.LAST_MONTH)
    assert tf.start_date == date(2024, 7, 1)
    assert tf.end_date == date(2024, 7, 31)


def test_last_month_crosses_a_year_boundary(resolver):
    resolver = TimeExpressionResolver(reference_date=date(2024, 1, 10), min_date=date(2020, 1, 1))
    tf = resolver.resolve_time_filter(TimeExpression.LAST_MONTH)
    assert tf.start_date == date(2023, 12, 1)
    assert tf.end_date == date(2023, 12, 31)


def test_this_quarter_starts_at_quarter_boundary_and_ends_at_reference_date(resolver):
    # 2024-08-15 is in Q3 (Jul-Sep)
    tf = resolver.resolve_time_filter(TimeExpression.THIS_QUARTER)
    assert tf.start_date == date(2024, 7, 1)
    assert tf.end_date == date(2024, 8, 15)


def test_last_quarter_is_the_full_preceding_calendar_quarter(resolver):
    tf = resolver.resolve_time_filter(TimeExpression.LAST_QUARTER)
    assert tf.start_date == date(2024, 4, 1)
    assert tf.end_date == date(2024, 6, 30)


def test_last_quarter_crosses_a_year_boundary(resolver):
    resolver = TimeExpressionResolver(reference_date=date(2024, 2, 1), min_date=date(2020, 1, 1))
    tf = resolver.resolve_time_filter(TimeExpression.LAST_QUARTER)
    assert tf.start_date == date(2023, 10, 1)
    assert tf.end_date == date(2023, 12, 31)


def test_this_year_starts_jan_1_and_ends_at_reference_date(resolver):
    tf = resolver.resolve_time_filter(TimeExpression.THIS_YEAR)
    assert tf.start_date == date(2024, 1, 1)
    assert tf.end_date == date(2024, 8, 15)


def test_last_year_is_the_full_preceding_calendar_year(resolver):
    tf = resolver.resolve_time_filter(TimeExpression.LAST_YEAR)
    assert tf.start_date == date(2023, 1, 1)
    assert tf.end_date == date(2023, 12, 31)


def test_all_time_spans_min_date_to_reference_date(resolver):
    tf = resolver.resolve_time_filter(TimeExpression.ALL_TIME)
    assert tf.start_date == date(2022, 1, 1)
    assert tf.end_date == date(2024, 8, 15)


def test_growth_spec_comparison_period_is_immediately_preceding_and_equal_length(resolver):
    growth = resolver.resolve_growth_spec(TimeExpression.THIS_MONTH)
    # current: Aug 1-15 (15 days) -> comparison: 15 days ending Jul 31
    assert growth.current_period.start_date == date(2024, 8, 1)
    assert growth.current_period.end_date == date(2024, 8, 15)
    assert growth.comparison_period.start_date == date(2024, 7, 17)
    assert growth.comparison_period.end_date == date(2024, 7, 31)
    current_len = (growth.current_period.end_date - growth.current_period.start_date).days
    comparison_len = (growth.comparison_period.end_date - growth.comparison_period.start_date).days
    assert current_len == comparison_len


@pytest.mark.parametrize(
    ("reference", "expr", "current", "comparison"),
    [
        # Found on real Olist data: Q2 (91 days) was compared with Dec 31-Mar 31, not Q1 (90 days).
        (date(2018, 8, 15), TimeExpression.LAST_QUARTER, (date(2018, 4, 1), date(2018, 6, 30)), (date(2018, 1, 1), date(2018, 3, 31))),
        (date(2024, 2, 1), TimeExpression.LAST_QUARTER, (date(2023, 10, 1), date(2023, 12, 31)), (date(2023, 7, 1), date(2023, 9, 30))),
        # March vs February, not a 31-day window starting Jan 29/30.
        (date(2023, 4, 10), TimeExpression.LAST_MONTH, (date(2023, 3, 1), date(2023, 3, 31)), (date(2023, 2, 1), date(2023, 2, 28))),
        (date(2024, 1, 10), TimeExpression.LAST_MONTH, (date(2023, 12, 1), date(2023, 12, 31)), (date(2023, 11, 1), date(2023, 11, 30))),
        # A leap year is one day longer than the year before it.
        (date(2025, 3, 1), TimeExpression.LAST_YEAR, (date(2024, 1, 1), date(2024, 12, 31)), (date(2023, 1, 1), date(2023, 12, 31))),
    ],
)
def test_growth_over_a_complete_calendar_period_compares_with_the_preceding_calendar_period(reference, expr, current, comparison):
    growth = TimeExpressionResolver(reference_date=reference, min_date=date(2016, 1, 1)).resolve_growth_spec(expr)
    assert (growth.current_period.start_date, growth.current_period.end_date) == current
    assert (growth.comparison_period.start_date, growth.comparison_period.end_date) == comparison


def test_growth_spec_periods_never_overlap(resolver):
    for expr in TimeExpression:
        growth = resolver.resolve_growth_spec(expr)
        assert growth.comparison_period.end_date < growth.current_period.start_date


def test_min_date_after_reference_date_is_rejected():
    with pytest.raises(ValueError):
        TimeExpressionResolver(reference_date=date(2020, 1, 1), min_date=date(2024, 1, 1))
