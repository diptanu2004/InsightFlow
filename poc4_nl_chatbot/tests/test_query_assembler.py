from datetime import date

import pytest

from insightflow_core.models import OperationType

from insightflow.models.intent import QuestionIntent, QuestionOperation
from insightflow.models.time_expression import TimeExpression
from insightflow.services.query_assembler import QueryAssembler
from insightflow.services.time_resolver import TimeExpressionResolver


@pytest.fixture
def assembler():
    resolver = TimeExpressionResolver(reference_date=date(2024, 8, 15), min_date=date(2022, 1, 1))
    return QueryAssembler(resolver)


def test_aggregate_with_no_time_expression_has_no_time_filter(assembler):
    intent = QuestionIntent(answerable=True, metric_name="revenue", operation=QuestionOperation.AGGREGATE)
    resolved = assembler.assemble(intent)
    (query,) = resolved.queries
    assert query.operation == OperationType.AGGREGATE
    assert query.metric == "revenue"
    assert query.time_filter is None


def test_aggregate_with_time_expression_resolves_a_time_filter(assembler):
    intent = QuestionIntent(
        answerable=True,
        metric_name="revenue",
        operation=QuestionOperation.AGGREGATE,
        time_expression=TimeExpression.THIS_MONTH,
    )
    resolved = assembler.assemble(intent)
    (query,) = resolved.queries
    assert query.time_filter.start_date == date(2024, 8, 1)
    assert query.time_filter.end_date == date(2024, 8, 15)


def test_group_by_produces_one_query_with_dimension(assembler):
    intent = QuestionIntent(answerable=True, metric_name="revenue", operation=QuestionOperation.GROUP_BY, dimension="category")
    resolved = assembler.assemble(intent)
    (query,) = resolved.queries
    assert query.operation == OperationType.GROUP_BY
    assert query.dimension == "category"


def test_growth_produces_one_query_with_growth_spec_no_dimension(assembler):
    intent = QuestionIntent(
        answerable=True,
        metric_name="revenue_growth",
        operation=QuestionOperation.GROWTH,
        time_expression=TimeExpression.LAST_QUARTER,
    )
    resolved = assembler.assemble(intent)
    (query,) = resolved.queries
    assert query.operation == OperationType.GROWTH
    assert query.dimension is None
    assert query.growth is not None
    assert query.time_filter is None


def test_growth_by_dimension_decomposes_into_two_group_by_queries(assembler):
    intent = QuestionIntent(
        answerable=True,
        metric_name="revenue",
        operation=QuestionOperation.GROWTH_BY_DIMENSION,
        dimension="category",
        time_expression=TimeExpression.LAST_QUARTER,
    )
    resolved = assembler.assemble(intent)
    assert len(resolved.queries) == 2
    current_query, comparison_query = resolved.queries
    for query in (current_query, comparison_query):
        assert query.operation == OperationType.GROUP_BY
        assert query.metric == "revenue"
        assert query.dimension == "category"
        assert query.time_filter is not None
    # current period is strictly after the comparison period, never overlapping
    assert comparison_query.time_filter.end_date < current_query.time_filter.start_date


def test_assemble_rejects_an_unanswerable_intent(assembler):
    intent = QuestionIntent(answerable=False, reason="no such metric")
    with pytest.raises(ValueError):
        assembler.assemble(intent)
