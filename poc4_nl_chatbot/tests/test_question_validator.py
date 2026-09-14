from insightflow_core.models import AnalyticalQuery, OperationType

from insightflow_chatbot.models.resolved import ResolvedQuery
from insightflow_chatbot.models.intent import QuestionIntent, QuestionOperation


def _resolved(query: AnalyticalQuery, *, operation=QuestionOperation.AGGREGATE, **intent_kwargs) -> ResolvedQuery:
    intent = QuestionIntent(answerable=True, metric_name=query.metric, operation=operation, **intent_kwargs)
    return ResolvedQuery(intent=intent, queries=[query])


def test_valid_aggregate_query_passes(question_validator):
    query = AnalyticalQuery(operation=OperationType.AGGREGATE, metric="revenue")
    result = question_validator.validate(_resolved(query))
    assert result.is_valid


def test_unregistered_metric_is_rejected(question_validator):
    query = AnalyticalQuery(operation=OperationType.AGGREGATE, metric="conversion_rate")
    result = question_validator.validate(_resolved(query))
    assert not result.is_valid
    assert any(e.code == "unknown_metric" for e in result.errors)


def test_unknown_dimension_is_rejected(question_validator):
    query = AnalyticalQuery(operation=OperationType.GROUP_BY, metric="revenue", dimension="star_sign")
    result = question_validator.validate(_resolved(query, operation=QuestionOperation.GROUP_BY, dimension="star_sign"))
    assert not result.is_valid
    assert any(e.code == "unknown_dimension" for e in result.errors)


def test_growth_kind_metric_grouped_by_dimension_is_rejected(question_validator):
    # revenue_growth is kind=GROWTH; ASTValidator only allows GROUP_BY against a bare measure or
    # a BASE-kind metric -- exactly the constraint class_diagram.md's "why GROWTH_BY_DIMENSION's
    # two queries must both target a BASE-kind metric" note is about.
    query = AnalyticalQuery(operation=OperationType.GROUP_BY, metric="revenue_growth", dimension="category")
    result = question_validator.validate(_resolved(query, operation=QuestionOperation.GROUP_BY, dimension="category"))
    assert not result.is_valid
    assert any(e.code == "group_by_unsupported_for_metric_kind" for e in result.errors)


def test_errors_across_multiple_queries_are_aggregated(question_validator):
    # Simulates a GROWTH_BY_DIMENSION ResolvedQuery where both decomposed queries are invalid.
    bad_query_1 = AnalyticalQuery(operation=OperationType.GROUP_BY, metric="conversion_rate", dimension="category")
    bad_query_2 = AnalyticalQuery(operation=OperationType.GROUP_BY, metric="conversion_rate", dimension="category")
    intent = QuestionIntent(
        answerable=True,
        metric_name="conversion_rate",
        operation=QuestionOperation.GROUP_BY,
        dimension="category",
    )
    resolved = ResolvedQuery(intent=intent, queries=[bad_query_1, bad_query_2])
    result = question_validator.validate(resolved)
    assert not result.is_valid
    assert len(result.errors) == 2


def test_grouping_an_answer_by_a_timestamp_or_amount_is_refused(question_validator):
    """Phase 8: the first real Olist dashboard grouped by raw timestamps and by price. Grouping a
    chat answer the same way gives one row per distinct value, not a breakdown."""
    for dimension in ("transaction_date", "revenue"):
        query = AnalyticalQuery(operation=OperationType.GROUP_BY, metric="orders", dimension=dimension)
        result = question_validator.validate(_resolved(query, operation=QuestionOperation.GROUP_BY, dimension=dimension))
        assert any(e.code == "dimension_not_supported" for e in result.errors), dimension


def test_grouping_by_a_category_is_still_accepted(question_validator):
    query = AnalyticalQuery(operation=OperationType.GROUP_BY, metric="revenue", dimension="category")
    result = question_validator.validate(_resolved(query, operation=QuestionOperation.GROUP_BY, dimension="category"))
    assert result.is_valid, result.errors
