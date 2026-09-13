"""QuestionPlanner -- one of POC 4's two LLM calls. Turns a natural-language question into a
structured QuestionIntent; never computes a number itself (QuestionExecutor does that, against
insightflow_core's unmodified engine, after QuestionValidator has approved the assembled query).
See docs/hld.md's pipeline diagram.

Uses QuestionIntent itself as the structured-output schema, not a separate loosely-typed LLM
schema -- its own pydantic validators (e.g. "GROUP_BY requires dimension", "answerable=False
requires reason") already enforce real structural constraints, so a malformed LLM response fails
immediately as a pydantic ValidationError. Same reasoning as POC 3's DashboardPlanner using
DashboardSpec itself as the schema.

Depends on the vendored LLMClient ABC (llm/client.py), not GroqLLMClient directly -- tests inject
a FakeLLMClient instead of making a real Groq call, same pattern POC 1/3 use.
"""
from insightflow.llm.client import LLMClient
from insightflow.models.intent import QuestionIntent
from insightflow.services.planner_context import PlannerContext


class QuestionPlanner:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    def plan(self, question: str, context: PlannerContext) -> QuestionIntent:
        return self.llm_client.generate_structured(self._build_prompt(question, context), QuestionIntent)

    def _build_prompt(self, question: str, context: PlannerContext) -> str:
        metrics_block = "\n".join(f"- {m.name} ({m.kind}): {m.description}" for m in context.available_metrics)
        time_expressions_block = ", ".join(t.value for t in context.supported_time_expressions)
        groupable_block = ", ".join(context.groupable_metrics) or "(none)"
        growth_block = ", ".join(context.growth_metrics) or "(none)"

        return (
            "You are a question-understanding assistant for an e-commerce analytics platform. "
            "Translate the business question below into a structured plan, using ONLY the "
            "metrics, dimensions, and time expressions listed -- never invent a name that isn't "
            "listed.\n\n"
            f"Entities in this business's data: {', '.join(context.entities)}\n"
            f"Available dimensions (usable as `dimension`): {', '.join(context.available_dimensions) or '(none)'}\n\n"
            f"Available metrics:\n{metrics_block}\n\n"
            f"Metrics usable for operation=group_by or growth_by_dimension (must be a bare "
            f"measure or a base-kind metric): {groupable_block}\n"
            f"Metrics usable for operation=growth (must be a growth-kind metric): {growth_block}\n"
            "A growth-kind metric can NEVER be used with group_by or growth_by_dimension; a "
            "groupable metric can NEVER be used with plain operation=growth -- pick the metric "
            "that matches the operation the question actually needs. These two restrictions "
            "apply ONLY to group_by/growth_by_dimension/growth -- ANY metric listed under "
            "\"Available metrics\" above (including ones not listed in either restriction, e.g. "
            "a ratio or having_ratio metric) is valid for plain operation=aggregate.\n\n"
            f"Allowed time expressions: {time_expressions_block}. Never emit a literal date -- "
            "always classify the question's timeframe into one of these values. If the question "
            "doesn't mention a timeframe at all, omit time_expression (it will be treated as "
            "all_time for aggregate/group_by, but is REQUIRED for growth and "
            "growth_by_dimension).\n\n"
            "Operations:\n"
            "- aggregate: a single overall number for a metric (dimension must be omitted)\n"
            "- group_by: a metric broken down by one dimension (dimension required)\n"
            "- growth: how much a growth-kind metric changed between two periods (dimension "
            "must be omitted, time_expression required)\n"
            "- growth_by_dimension: which values of a dimension drove a change in a metric "
            "between two periods (dimension AND time_expression required, metric must be "
            "groupable, never a growth-kind metric)\n\n"
            "If the question cannot be answered with the metrics/dimensions listed above -- for "
            "example it needs data this platform doesn't have (session/visitor data, marketing "
            "spend, inventory, etc.) or asks about a metric/dimension genuinely not listed -- set "
            "answerable=false and give a one-sentence `reason` explaining specifically what's "
            "missing. Do not guess or force-fit a question onto the closest available metric.\n\n"
            f'Question: "{question}"'
        )
