import pytest
from pydantic import ValidationError

from insightflow.models.intent import QuestionIntent, QuestionOperation
from insightflow.models.time_expression import TimeExpression


def test_unanswerable_requires_a_reason():
    with pytest.raises(ValidationError):
        QuestionIntent(answerable=False)


def test_unanswerable_with_reason_is_valid():
    intent = QuestionIntent(answerable=False, reason="no session data available")
    assert intent.metric_name is None


def test_answerable_requires_metric_and_operation():
    with pytest.raises(ValidationError):
        QuestionIntent(answerable=True)


def test_group_by_requires_dimension():
    with pytest.raises(ValidationError):
        QuestionIntent(answerable=True, metric_name="revenue", operation=QuestionOperation.GROUP_BY)


def test_aggregate_rejects_a_dimension():
    with pytest.raises(ValidationError):
        QuestionIntent(answerable=True, metric_name="revenue", operation=QuestionOperation.AGGREGATE, dimension="category")


def test_growth_requires_time_expression():
    with pytest.raises(ValidationError):
        QuestionIntent(answerable=True, metric_name="revenue_growth", operation=QuestionOperation.GROWTH)


def test_growth_by_dimension_requires_both_dimension_and_time_expression():
    with pytest.raises(ValidationError):
        QuestionIntent(
            answerable=True,
            metric_name="revenue",
            operation=QuestionOperation.GROWTH_BY_DIMENSION,
            dimension="category",
        )


def test_growth_by_dimension_with_both_fields_is_valid():
    intent = QuestionIntent(
        answerable=True,
        metric_name="revenue",
        operation=QuestionOperation.GROWTH_BY_DIMENSION,
        dimension="category",
        time_expression=TimeExpression.LAST_QUARTER,
    )
    assert intent.answerable
