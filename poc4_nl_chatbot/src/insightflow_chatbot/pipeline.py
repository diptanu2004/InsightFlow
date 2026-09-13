"""QuestionAnsweringPipeline -- POC 4's single orchestrator entrypoint, mirroring POC 2/3's
AnalyticsEnginePipeline.run() / DashboardGenerationPipeline.run() convention: the entire
integration surface a future FastAPI route calls directly (README.md's repo layout convention).

    pipeline = build_pipeline(semantic_model, registry, engine, llm_client, data_dir, ...)
    answer = pipeline.answer("How much did revenue grow last quarter?")
"""
from insightflow_core.compilation import FieldResolver
from insightflow_core.models import SemanticModel
from insightflow_core.pipeline import AnalyticsEnginePipeline
from insightflow_core.registry import MetricRegistry
from insightflow_core.validation import ASTValidator

from insightflow_chatbot.llm.client import LLMClient
from insightflow_chatbot.models.result import Answer
from insightflow_chatbot.services.date_bounds import infer_date_bounds
from insightflow_chatbot.services.executor import QuestionExecutor
from insightflow_chatbot.services.insight import InsightGenerator
from insightflow_chatbot.services.planner import QuestionPlanner
from insightflow_chatbot.services.planner_context import build_planner_context
from insightflow_chatbot.services.query_assembler import QueryAssembler
from insightflow_chatbot.services.result_differ import ResultDiffer
from insightflow_chatbot.services.time_resolver import TimeExpressionResolver
from insightflow_chatbot.services.validator import QuestionValidator


class QuestionAnsweringPipeline:
    def __init__(
        self,
        semantic_model: SemanticModel,
        registry: MetricRegistry,
        engine: AnalyticsEnginePipeline,
        planner: QuestionPlanner,
        assembler: QueryAssembler,
        validator: QuestionValidator,
        executor: QuestionExecutor,
        insight_generator: InsightGenerator,
    ):
        self.semantic_model = semantic_model
        self.registry = registry
        self.engine = engine
        self.planner = planner
        self.assembler = assembler
        self.validator = validator
        self.executor = executor
        self.insight_generator = insight_generator
        self._field_resolver = FieldResolver(semantic_model)

    def answer(self, question: str) -> Answer:
        context = build_planner_context(self.semantic_model, self.registry, self._field_resolver)
        intent = self.planner.plan(question, context)

        if not intent.answerable:
            return Answer(question=question, refused=True, reason=intent.reason, explanation=intent.reason, intent=intent)

        resolved = self.assembler.assemble(intent)
        validation = self.validator.validate(resolved)
        if not validation.is_valid:
            reason = "; ".join(e.message for e in validation.errors)
            return Answer(question=question, refused=True, reason=reason, explanation=reason, intent=intent)

        result = self.executor.execute(resolved)
        # Refusals short-circuit before this point -- InsightGenerator is only ever invoked for
        # an answerable, successfully-executed question (class_diagram.md's resolved "Insight
        # LLM prompt scope" question).
        explanation = self.insight_generator.explain(question, result)
        return Answer(question=question, refused=False, result=result, explanation=explanation, intent=intent)


def build_question_answering_pipeline(
    semantic_model: SemanticModel,
    registry: MetricRegistry,
    engine: AnalyticsEnginePipeline,
    llm_client: LLMClient,
    data_dir: str,
    *,
    time_entity: str = "orders",
    time_field: str = "transaction_date",
    max_row_limit: int = 1000,
) -> QuestionAnsweringPipeline:
    """Convenience wiring for the common case -- construct every stage from just a
    SemanticModel + MetricRegistry + a real engine + an LLM client, instead of a caller re-wiring
    seven constructors by hand every time. Not required (each stage can be constructed and
    injected directly -- that's what this package's own tests mostly do).
    """
    min_date, reference_date = infer_date_bounds(semantic_model, data_dir, time_entity, time_field)
    time_resolver = TimeExpressionResolver(reference_date=reference_date, min_date=min_date)
    ast_validator = ASTValidator(registry, semantic_model, max_row_limit)

    return QuestionAnsweringPipeline(
        semantic_model=semantic_model,
        registry=registry,
        engine=engine,
        planner=QuestionPlanner(llm_client),
        assembler=QueryAssembler(time_resolver),
        validator=QuestionValidator(ast_validator),
        executor=QuestionExecutor(engine, ResultDiffer()),
        insight_generator=InsightGenerator(llm_client),
    )
