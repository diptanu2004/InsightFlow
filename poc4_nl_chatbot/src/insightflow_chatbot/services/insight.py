"""InsightGenerator -- POC 4's second and last LLM call. Explains an already-verified
QuestionResult in natural language; never recomputes or overrides the numbers (hld.md's "LLMs
understand; deterministic systems calculate"). Never invoked for a refused question -- see
class_diagram.md's resolved "Insight LLM prompt scope" question; QuestionAnsweringPipeline sets a
refused Answer's explanation directly from the deterministic reason string instead.
"""
from pydantic import BaseModel

from insightflow_chatbot.llm.client import LLMClient
from insightflow_chatbot.models.result import QuestionResult


class _ExplanationOutput(BaseModel):
    explanation: str


class InsightGenerator:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    def explain(self, question: str, result: QuestionResult) -> str:
        prompt = self._build_prompt(question, result)
        return self.llm_client.generate_structured(prompt, _ExplanationOutput).explanation

    @staticmethod
    def _build_prompt(question: str, result: QuestionResult) -> str:
        if result.metric_result is not None:
            mr = result.metric_result
            # `shape` (not value-presence) is the authoritative signal for scalar vs. grouped --
            # a scalar result can legitimately have value=None (e.g. a GROWTH query whose
            # comparison period matched zero rows), which value-presence alone can't distinguish
            # from a grouped result. See insightflow_core's MetricResult.shape docstring.
            if mr.shape == "scalar":
                value_text = str(mr.value) if mr.value is not None else "undefined (mathematically undefined for this period)"
                data_block = f"{mr.metric_name} = {value_text}"
            else:
                rows_block = "\n".join(f"- {row}" for row in mr.rows)
                data_block = f"{mr.metric_name}, broken down by group:\n{rows_block}"
        else:
            deltas_block = "\n".join(
                f"- {d.dimension_value}: {d.comparison_value} -> {d.current_value} "
                f"(delta {d.delta:+.2f}, pct_change {d.pct_change if d.pct_change is not None else 'undefined (grew from zero)'})"
                for d in result.category_deltas
            )
            data_block = f"Per-category change between the two periods, most-declined first:\n{deltas_block}"

        return (
            "You are an analytics assistant. The verified numbers below were computed "
            "deterministically -- treat them as ground truth. Do not recompute, second-guess, or "
            "alter any number. Write a short (1-3 sentence) natural-language answer to the "
            "question, citing the actual figures.\n\n"
            f'Question: "{question}"\n\n'
            f"Verified data:\n{data_block}"
        )
