"""InsightGenerator -- POC 4's second and last LLM call. Explains an already-verified
QuestionResult in natural language; never recomputes or overrides the numbers (hld.md's "LLMs
understand; deterministic systems calculate"). Never invoked for a refused question -- see
class_diagram.md's resolved "Insight LLM prompt scope" question; QuestionAnsweringPipeline sets a
refused Answer's explanation directly from the deterministic reason string instead.
"""
from pydantic import BaseModel

from insightflow_chatbot.llm.client import LLMClient
from insightflow_chatbot.models.result import QuestionResult


_MAX_ROWS_IN_PROMPT = 20


def _format_value(value: float, format_: str) -> str:
    if format_ == "money":
        return f"{value:,.2f}"
    if format_ == "count":
        return f"{value:,.0f}"
    if format_ == "percent":
        return f"{value:.1%}"
    return f"{value:,.4g}"


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
                # Pre-formatted per the registry's display format: a raw float reached the explanation as
                # "3338648.129999977". Rounding for display only -- the verified value is untouched.
                value_text = (
                    _format_value(mr.value, mr.format)
                    if mr.value is not None
                    else "undefined (mathematically undefined for this period)"
                )
                data_block = f"{mr.metric_name} = {value_text}"
            else:
                # Bounded: sending every group once put 1,000 Olist cities (16k tokens) into this prompt
                # and the provider rejected it. The largest groups come first (the engine orders grouped
                # results by value), and the reader of the prompt is told what's left out.
                shown = mr.rows[:_MAX_ROWS_IN_PROMPT]
                rows_block = "\n".join(f"- {row}" for row in shown)
                hidden = len(mr.rows) - len(shown)
                data_block = f"{mr.metric_name}, broken down by group (largest first):\n{rows_block}"
                if hidden > 0 or mr.truncated:
                    data_block += (
                        f"\n({hidden} more groups not listed here"
                        + ("; the full list was also cut off, so do not state totals or counts of groups" if mr.truncated else "")
                        + ")"
                    )
            if mr.caveats:
                # Stated by the engine, not inferred -- see insightflow_core's MetricResult.caveats.
                data_block += "\nCAVEAT: " + " ".join(mr.caveats)
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
            "question, citing the actual figures. If the data carries a CAVEAT, do not draw any "
            "conclusion from that value -- say it can't be measured reliably from this dataset, and why. "
            "Nothing in the data says which currency amounts are in, so never write a currency symbol "
            "or code.\n\n"
            f'Question: "{question}"\n\n'
            f"Verified data:\n{data_block}"
        )
