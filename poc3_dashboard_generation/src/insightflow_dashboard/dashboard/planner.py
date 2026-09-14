"""DashboardPlanner -- the ONE LLM call in POC 3. Decides which components go on the dashboard;
never computes a number itself (DashboardDataResolver does that, against POC 2's engine, after
DashboardValidator has approved the shape). See docs/hld.md's "Pipeline" section.

Uses DashboardSpec itself as the structured-output schema, not a separate loosely-typed LLM
schema -- DashboardSpec's own pydantic validators (ComponentSpec's "dimension required for
grouping types", "KPI must not set a dimension") already enforce real structural constraints, so
a malformed LLM response fails immediately as a pydantic ValidationError rather than silently
passing through as an invalid spec. This mirrors AnalyticalQuery's own structural pydantic
validators in POC 2 (models/query.py) -- structural validity is a job for the schema itself;
DashboardValidator (validator.py) is the separate, deterministic SEMANTIC check on top (does the
metric/dimension actually exist), same split POC 2 draws between AnalyticalQuery's validators and
ASTValidator.

Depends on the vendored LLMClient ABC (llm/client.py), not GroqLLMClient directly -- tests inject
a FakeLLMClient instead of making a real Groq call, same pattern POC 1's SemanticMapper uses.
"""
from insightflow_dashboard.dashboard.planner_context import PlannerContext
from insightflow_dashboard.dashboard.spec import DashboardSpec
from insightflow_dashboard.llm.client import LLMClient


class DashboardPlanner:
    def __init__(self, llm_client: LLMClient, min_components: int, max_components: int):
        self.llm_client = llm_client
        self.min_components = min_components
        self.max_components = max_components

    def plan(self, context: PlannerContext) -> DashboardSpec:
        return self.llm_client.generate_structured(self._build_prompt(context), DashboardSpec)

    def _build_prompt(self, context: PlannerContext) -> str:
        metrics_block = "\n".join(f"- {m.name} ({m.kind}): {m.description}" for m in context.available_metrics)
        signals_block = (
            "\n".join(f"- {s.name}: {self._format_signal(s)}" for s in context.signals)
            or "(no diagnostic signals could be computed for this dataset)"
        )
        component_types_block = ", ".join(t.value for t in context.supported_component_types)

        groupable_block = (
            "\n".join(f"- {metric}: {', '.join(dims)}" for metric, dims in context.groupable_dimensions.items())
            or "(none -- this dataset supports no grouped components, so use only kpi components)"
        )
        resolvable_metrics_block = ", ".join(context.resolvable_metrics) or "(none)"

        return (
            "You are a dashboard planning assistant for an e-commerce analytics platform.\n"
            "Design a dashboard for the business described below, using ONLY the metrics, "
            "dimensions, and component types listed -- never invent a metric or dimension name "
            "that isn't listed, and never use a component type that isn't listed.\n\n"
            f"Entities in this business's data: {', '.join(context.entities)}\n"
            f"Available dimensions: {', '.join(context.available_dimensions) or '(none)'}\n\n"
            f"Available metrics (for context, including ones you cannot set as a component's "
            f"metric_name -- see below):\n{metrics_block}\n\n"
            f"Metrics usable as any component's `metric_name`: {resolvable_metrics_block}\n"
            "Growth metrics (kind=growth, e.g. revenue_growth) are NEVER usable as a component's "
            "metric_name, in a kpi or any other component -- they can only be referenced by value "
            "from the diagnostic signals below, in a component's rationale or the dashboard's "
            "narrative text.\n\n"
            "Metrics usable in a bar_chart, pie_chart, or table (i.e. with a `dimension` set), each "
            "followed by the ONLY dimensions it can be grouped by in this dataset -- any pairing "
            f"not listed here cannot be computed:\n{groupable_block}\n"
            "Any other resolvable metric (kind ratio or having_ratio, e.g. an average or a "
            "repeat-purchase rate) can ONLY be used in a kpi component, never grouped by a "
            "dimension.\n\n"
            f"Diagnostic signals already computed for this dataset (react to these -- e.g. "
            "emphasize revenue trend and category breakdown if revenue is declining):\n"
            f"{signals_block}\n\n"
            "Positive growth signals do not by themselves mean the business is healthy. A "
            "resolvable metric you choose to query directly (for example a having_ratio metric "
            "such as a repeat-purchase rate) can reveal a problem the growth signals above hide "
            "entirely -- e.g. revenue/order/customer growth all look healthy while almost none of "
            "that growth comes from repeat business, meaning it depends entirely on continuously "
            "acquiring new customers. If a metric you include tells a different or contradicting "
            "story than the growth signals above, you MUST name that tension explicitly in the "
            "dashboard's `narrative` and in that component's `rationale` -- do not describe the "
            "signals as if they were independently good news.\n\n"
            "Ground every claim in the diagnostic signals above. The `narrative` and each "
            "`rationale` may only describe a metric's value (high, low, healthy, declining, driven "
            "by ...) if that value appears in the signals list -- you do not see the values of the "
            "components you choose, so never characterize a metric that isn't listed there. A signal "
            "marked CAVEAT is not evidence of anything: never build a conclusion on it; if you mention "
            "it at all, say it could not be measured reliably and why. Nothing in the data says which "
            "currency amounts are in, so never write a currency symbol or code.\n\n"
            f"Allowed component types: {component_types_block}\n\n"
            f"Produce between {self.min_components} and {self.max_components} components. Every "
            "component's `metric_name` MUST be one of the metrics usable as a component's "
            "metric_name listed above. Every non-KPI component (bar_chart, pie_chart, table) MUST "
            "set `metric_name` to one of the metrics usable in a grouping component listed above "
            "AND set `dimension` to one of the dimensions listed for THAT metric. KPI components MUST NOT "
            "set `dimension`. Give each component a short, unique, snake_case `component_id`. Set "
            "`rationale` on every component to a one-sentence reason tied to the signals above, and "
            "set the dashboard's own `narrative` to a one- or two-sentence VERDICT on what the data "
            "actually shows about the business's health -- reconciling any tension between the "
            "growth signals and the metrics you chose, not just a list of what was included. Do "
            "not use `time_grain` or the line_chart component type unless it is explicitly listed "
            "as an allowed component type above."
        )

    @staticmethod
    def _format_signal(signal) -> str:
        # `shape` (not value-presence) is the authoritative signal for scalar vs. grouped --
        # SignalGatherer never actually hands this an undefined scalar (it filters those out
        # itself), but branching on `shape` here is still the correct check, not an inference.
        result = signal.result
        text = f"{result.value:.2f}" if result.shape == "scalar" else str(result.rows)
        if result.caveats:
            return f"{text} [CAVEAT -- do not draw any conclusion from this value: {' '.join(result.caveats)}]"
        return text
