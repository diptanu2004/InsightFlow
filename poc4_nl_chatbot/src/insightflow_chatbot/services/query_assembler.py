"""Deterministic AST assembly -- see class_diagram.md's QueryAssembler. Turns a QuestionIntent
(planner output, closed enum for time) into one or two real insightflow_core AnalyticalQuery
objects, never invented ad hoc downstream.
"""
from insightflow_core.models import AnalyticalQuery, OperationType

from insightflow_chatbot.models.intent import QuestionIntent, QuestionOperation
from insightflow_chatbot.models.resolved import ResolvedQuery
from insightflow_chatbot.models.time_expression import TimeExpression
from insightflow_chatbot.services.time_resolver import TimeExpressionResolver


class QueryAssembler:
    def __init__(self, time_resolver: TimeExpressionResolver):
        self.time_resolver = time_resolver

    def assemble(self, intent: QuestionIntent) -> ResolvedQuery:
        if not intent.answerable:
            raise ValueError("cannot assemble a query from an unanswerable intent")
        if intent.operation == QuestionOperation.GROWTH_BY_DIMENSION:
            queries = self._assemble_growth_by_dimension(intent)
        else:
            queries = [self._assemble_simple(intent)]
        return ResolvedQuery(intent=intent, queries=queries)

    def _resolve_optional_time_filter(self, intent: QuestionIntent):
        # No time_expression and ALL_TIME both mean "no restriction" for AGGREGATE/GROUP_BY --
        # omit time_filter entirely rather than resolving ALL_TIME to an explicit bounded window.
        # Found via POC 4's real-Olist integration test: a metric whose entity has no time field
        # (real Olist's "revenue", on `payments`) is valid when queried unfiltered, but an
        # explicit ALL_TIME TimeFilter would be (correctly) rejected by ASTValidator's
        # time_filter_entity_missing_time_field check -- "all time" must behave identically to
        # "no filter" for such a metric, not merely as wide a filter as this dataset allows.
        if intent.time_expression is None or intent.time_expression == TimeExpression.ALL_TIME:
            return None
        return self.time_resolver.resolve_time_filter(intent.time_expression)

    def _assemble_simple(self, intent: QuestionIntent) -> AnalyticalQuery:
        if intent.operation == QuestionOperation.AGGREGATE:
            return AnalyticalQuery(
                operation=OperationType.AGGREGATE, metric=intent.metric_name, time_filter=self._resolve_optional_time_filter(intent)
            )
        if intent.operation == QuestionOperation.GROUP_BY:
            return AnalyticalQuery(
                operation=OperationType.GROUP_BY,
                metric=intent.metric_name,
                dimension=intent.dimension,
                time_filter=self._resolve_optional_time_filter(intent),
            )
        if intent.operation == QuestionOperation.GROWTH:
            growth = self.time_resolver.resolve_growth_spec(intent.time_expression)
            return AnalyticalQuery(operation=OperationType.GROWTH, metric=intent.metric_name, growth=growth)
        raise ValueError(f"unhandled QuestionOperation in _assemble_simple: {intent.operation}")

    def _assemble_growth_by_dimension(self, intent: QuestionIntent) -> list[AnalyticalQuery]:
        # No single AnalyticalQuery can express "growth, grouped by dimension" -- dimension is
        # only valid with operation=GROUP_BY, growth only with operation=GROWTH, mutually
        # exclusive by AnalyticalQuery's own validator. Decompose into two ordinary GROUP_BY
        # queries instead; QuestionExecutor runs both and ResultDiffer computes the deltas. See
        # class_diagram.md's banner note for the full reasoning.
        growth = self.time_resolver.resolve_growth_spec(intent.time_expression)
        current_query = AnalyticalQuery(
            operation=OperationType.GROUP_BY,
            metric=intent.metric_name,
            dimension=intent.dimension,
            time_filter=growth.current_period,
        )
        comparison_query = AnalyticalQuery(
            operation=OperationType.GROUP_BY,
            metric=intent.metric_name,
            dimension=intent.dimension,
            time_filter=growth.comparison_period,
        )
        return [current_query, comparison_query]
