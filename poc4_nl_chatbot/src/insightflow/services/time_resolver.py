"""Deterministic relative-date resolution -- see hld.md's "Relative dates resolve
deterministically, not via LLM date arithmetic" and class_diagram.md's TimeExpressionResolver.

`reference_date` and `min_date` are both required constructor inputs, never computed from
`date.today()` or hardcoded: a static historical dataset (Olist ends in 2018) makes wall-clock
"today" meaningless for resolving LAST_QUARTER, and ALL_TIME needs a real lower bound rather than
an arbitrary one -- ASTValidator._validate_time_filter rejects any TimeFilter spanning more than
~20 years, so a hardcoded epoch like 1900-01-01 would make ALL_TIME fail validation outright on
any real dataset. Both are meant to come from a one-time MIN/MAX(order_date)-style signal query
run once at QuestionAnsweringPipeline construction (class_diagram.md's resolved "where reference_
date is sourced" question) -- this class has no opinion on how they were obtained.
"""
from datetime import date, timedelta

from insightflow_core.models import GrowthSpec, TimeFilter

from insightflow.models.time_expression import TimeExpression


class TimeExpressionResolver:
    def __init__(self, reference_date: date, min_date: date):
        if min_date > reference_date:
            raise ValueError("min_date must not be after reference_date")
        self.reference_date = reference_date
        self.min_date = min_date

    def resolve_time_filter(self, expr: TimeExpression) -> TimeFilter:
        start, end = self._period_bounds(expr)
        return TimeFilter(start_date=start, end_date=end)

    def resolve_growth_spec(self, expr: TimeExpression) -> GrowthSpec:
        current = self.resolve_time_filter(expr)
        comparison = self._preceding_equal_length_period(current)
        return GrowthSpec(current_period=current, comparison_period=comparison)

    def _period_bounds(self, expr: TimeExpression) -> tuple[date, date]:
        ref = self.reference_date
        if expr == TimeExpression.THIS_MONTH:
            return ref.replace(day=1), ref
        if expr == TimeExpression.LAST_MONTH:
            last_of_prev = ref.replace(day=1) - timedelta(days=1)
            return last_of_prev.replace(day=1), last_of_prev
        if expr == TimeExpression.THIS_QUARTER:
            return self._quarter_start(ref), ref
        if expr == TimeExpression.LAST_QUARTER:
            this_q_start = self._quarter_start(ref)
            last_q_end = this_q_start - timedelta(days=1)
            return self._quarter_start(last_q_end), last_q_end
        if expr == TimeExpression.THIS_YEAR:
            return ref.replace(month=1, day=1), ref
        if expr == TimeExpression.LAST_YEAR:
            return date(ref.year - 1, 1, 1), date(ref.year - 1, 12, 31)
        if expr == TimeExpression.ALL_TIME:
            return self.min_date, ref
        raise ValueError(f"unhandled TimeExpression: {expr}")

    @staticmethod
    def _quarter_start(d: date) -> date:
        quarter_first_month = ((d.month - 1) // 3) * 3 + 1
        return date(d.year, quarter_first_month, 1)

    @staticmethod
    def _preceding_equal_length_period(period: TimeFilter) -> TimeFilter:
        length_days = (period.end_date - period.start_date).days + 1
        comparison_end = period.start_date - timedelta(days=1)
        comparison_start = comparison_end - timedelta(days=length_days - 1)
        return TimeFilter(start_date=comparison_start, end_date=comparison_end)
