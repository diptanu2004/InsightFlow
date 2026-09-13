"""Runs a validated ResolvedQuery through insightflow_core's unmodified engine and produces a
QuestionResult -- see class_diagram.md's QuestionExecutor.
"""
from insightflow_core.pipeline import AnalyticsEnginePipeline

from insightflow_chatbot.config import settings
from insightflow_chatbot.models.intent import QuestionOperation
from insightflow_chatbot.models.resolved import ResolvedQuery
from insightflow_chatbot.models.result import QuestionResult
from insightflow_chatbot.services.result_differ import ResultDiffer


class QuestionExecutor:
    def __init__(self, engine: AnalyticsEnginePipeline, differ: ResultDiffer):
        self.engine = engine
        self.differ = differ

    def execute(self, resolved: ResolvedQuery) -> QuestionResult:
        if resolved.intent.operation == QuestionOperation.GROWTH_BY_DIMENSION:
            current_query, comparison_query = resolved.queries
            current_result = self.engine.run(current_query)
            comparison_result = self.engine.run(comparison_query)
            deltas = self.differ.diff(current_result, comparison_result, resolved.intent.dimension)
            return QuestionResult(category_deltas=deltas[: settings.top_n_category_deltas])

        (query,) = resolved.queries
        return QuestionResult(metric_result=self.engine.run(query))
