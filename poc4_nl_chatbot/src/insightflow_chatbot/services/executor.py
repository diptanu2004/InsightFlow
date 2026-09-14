"""Runs a validated ResolvedQuery through insightflow_core's unmodified engine and produces a
QuestionResult -- see class_diagram.md's QuestionExecutor.
"""
from insightflow_core.pipeline import AnalyticsEnginePipeline

from insightflow_chatbot.config import settings
from insightflow_chatbot.models.intent import QuestionOperation
from insightflow_chatbot.models.resolved import ResolvedQuery
from insightflow_chatbot.models.result import QuestionResult
from insightflow_chatbot.services.result_differ import ResultDiffer


class IncompleteComparison(ValueError):
    """A per-group period comparison can't be computed honestly because a result was cut off."""


class QuestionExecutor:
    def __init__(self, engine: AnalyticsEnginePipeline, differ: ResultDiffer):
        self.engine = engine
        self.differ = differ

    def execute(self, resolved: ResolvedQuery) -> QuestionResult:
        if resolved.intent.operation == QuestionOperation.GROWTH_BY_DIMENSION:
            current_query, comparison_query = resolved.queries
            current_result = self.engine.run(current_query)
            comparison_result = self.engine.run(comparison_query)
            if current_result.truncated or comparison_result.truncated:
                # ResultDiffer zero-fills a group missing from either side, which is only right when
                # both lists are complete. On real Olist (~2,100 cities per quarter, 1,000-row cap) a
                # city cut from one list became a fake collapse -- Brasilia's 442 orders read as
                # "461 -> 0". Refuse rather than invent declines.
                raise IncompleteComparison(
                    f'there are more "{resolved.intent.dimension}" values than can be compared in one '
                    f"query ({max(len(current_result.rows or []), len(comparison_result.rows or []))}+ per "
                    "period), so a change per value would be computed on incomplete lists"
                )
            deltas = self.differ.diff(current_result, comparison_result, resolved.intent.dimension)
            return QuestionResult(
                category_deltas=deltas[: settings.top_n_category_deltas],
                # Deltas are values of the base metric; without this a renderer can't tell an amount
                # from a count (the diffing step drops each MetricResult's own format).
                format=current_result.format,
            )

        (query,) = resolved.queries
        return QuestionResult(metric_result=self.engine.run(query))
