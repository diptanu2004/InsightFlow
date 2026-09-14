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
from insightflow_chatbot.models.intent import QuestionIntent, QuestionOperation
from insightflow_chatbot.models.result import Answer
from insightflow_chatbot.models.time_expression import TimeExpression
from insightflow_chatbot.services.date_bounds import AmbiguousTimeField, NoTimeField, infer_date_bounds
from insightflow_chatbot.services.executor import IncompleteComparison, QuestionExecutor
from insightflow_chatbot.services.insight import InsightGenerator
from insightflow_chatbot.services.planner import QuestionPlanner
from insightflow_chatbot.services.planner_context import build_planner_context
from insightflow_chatbot.services.query_assembler import QueryAssembler
from insightflow_chatbot.services.result_differ import ResultDiffer
from insightflow_chatbot.services.time_resolver import TimeExpressionResolver
from insightflow_chatbot.services.validator import QuestionValidator


_NO_DATE_REASON = (
    "This dataset has no confirmed date column, so questions about a time period (such as last quarter, "
    "or growth between periods) can't be answered. Questions over all of the data still work. If a date "
    "column is awaiting review in the Data section, confirming it makes period questions answerable."
)


def _values_filter_reason(intent: QuestionIntent, available_dimensions: list[str]) -> str:
    values = ", ".join(intent.dimension_values)
    metric = (intent.metric_name or "the metric").replace("_", " ")
    if intent.dimension:
        by = f" by {intent.dimension.replace('_', ' ')}"
    elif len(available_dimensions) == 1:
        by = f" by {available_dimensions[0].replace('_', ' ')}"
    elif available_dimensions:
        # The planner didn't say which dimension the values belong to (it can't set one on a plain aggregate),
        # so name the real options rather than "the relevant dimension".
        names = [d.replace("_", " ") for d in available_dimensions]
        by = f" by {', '.join(names[:-1])} or {names[-1]}"
    else:
        by = " broken down by a dimension"
    return (
        f"Answers can't be limited to specific values ({values}) yet, and adding up values isn't something the "
        f"AI is allowed to do. Ask for {metric}{by} to see each value, including these."
    )


def _needs_a_period(intent: QuestionIntent) -> bool:
    if intent.operation in (QuestionOperation.GROWTH, QuestionOperation.GROWTH_BY_DIMENSION):
        return True
    return intent.time_expression not in (None, TimeExpression.ALL_TIME)


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
        periods_unavailable_reason: str | None = None,
    ):
        self.semantic_model = semantic_model
        # Set when the assembler has no time resolver: why period questions are refused for this dataset.
        self.periods_unavailable_reason = periods_unavailable_reason
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
        time_resolver = self.assembler.time_resolver
        data_through = time_resolver.reference_date if time_resolver is not None else None

        if not intent.answerable:
            return Answer(
                question=question, refused=True, reason=intent.reason, explanation=intent.reason, intent=intent, data_through=data_through
            )

        if intent.dimension_values:
            reason = _values_filter_reason(intent, context.available_dimensions)
            return Answer(question=question, refused=True, reason=reason, explanation=reason, intent=intent, data_through=data_through)

        if time_resolver is None and _needs_a_period(intent):
            # Deterministic, like a validator refusal. A dataset without a usable date used to fail chat as a
            # whole (422 on every question, found on a real 4-file Olist upload with no orders file), even
            # for questions that need no dates at all.
            reason = self.periods_unavailable_reason or _NO_DATE_REASON
            return Answer(question=question, refused=True, reason=reason, explanation=reason, intent=intent)

        resolved = self.assembler.assemble(intent)
        validation = self.validator.validate(resolved)
        if not validation.is_valid:
            reason = "; ".join(e.message for e in validation.errors)
            return Answer(
                question=question, refused=True, reason=reason, explanation=reason, intent=intent,
                queries=resolved.queries, data_through=data_through,
            )

        try:
            result = self.executor.execute(resolved)
        except IncompleteComparison as exc:
            # A deterministic refusal, same shape as a validator refusal: the question was understood,
            # but the data can't support an honest answer to it.
            return Answer(
                question=question, refused=True, reason=str(exc), explanation=str(exc), intent=intent,
                queries=resolved.queries, data_through=data_through,
            )
        caveats = result.metric_result.caveats if result.metric_result is not None else []
        if caveats:
            # Deterministic explanation, no LLM -- same as a refusal. An instruction not to draw
            # conclusions from a caveated value wasn't a reliable guardrail on a real Olist dashboard,
            # so the explanation of one isn't left to an LLM at all.
            mr = result.metric_result
            value_text = (
                "undefined" if mr.shape == "scalar" and mr.value is None
                else str(mr.value) if mr.shape == "scalar"
                else f"{len(mr.rows)} groups"
            )
            explanation = f"{mr.metric_name} is {value_text}, but it can't be interpreted from this dataset: {' '.join(caveats)}"
            return Answer(
                question=question, refused=False, result=result, explanation=explanation, intent=intent,
                queries=resolved.queries, data_through=data_through,
            )
        # Refusals short-circuit before this point -- InsightGenerator is only ever invoked for
        # an answerable, successfully-executed question (class_diagram.md's resolved "Insight
        # LLM prompt scope" question).
        explanation = self.insight_generator.explain(question, result)
        return Answer(
            question=question, refused=False, result=result, explanation=explanation, intent=intent,
            queries=resolved.queries, data_through=data_through,
        )


def build_question_answering_pipeline(
    semantic_model: SemanticModel,
    registry: MetricRegistry,
    engine: AnalyticsEnginePipeline,
    llm_client: LLMClient,
    data_dir: str,
    *,
    time_entity: str | None = None,
    time_field: str = "transaction_date",
    max_row_limit: int = 1000,
) -> QuestionAnsweringPipeline:
    """Convenience wiring for the common case -- construct every stage from just a
    SemanticModel + MetricRegistry + a real engine + an LLM client, instead of a caller re-wiring
    seven constructors by hand every time. Not required (each stage can be constructed and
    injected directly -- that's what this package's own tests mostly do).
    """
    time_resolver: TimeExpressionResolver | None = None
    periods_unavailable_reason: str | None = None
    try:
        min_date, reference_date = infer_date_bounds(semantic_model, data_dir, time_entity, time_field)
        time_resolver = TimeExpressionResolver(reference_date=reference_date, min_date=min_date)
    except NoTimeField:
        periods_unavailable_reason = _NO_DATE_REASON
    except AmbiguousTimeField as exc:
        periods_unavailable_reason = (
            f"More than one file in this dataset has a confirmed date column ({', '.join(exc.entities)}), so which "
            "dates define a period is ambiguous and questions about a time period can't be answered. Questions "
            "over all of the data still work."
        )
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
        periods_unavailable_reason=periods_unavailable_reason,
    )
