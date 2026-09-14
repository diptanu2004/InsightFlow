from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class QueryMetadata(BaseModel):
    sql: str
    execution_time_ms: float
    row_count: int


class MetricResult(BaseModel):
    """One typed shape for both scalar and grouped results (hld.md's resolved open question):
    `value` for a plain KPI, `rows` for a grouped/dimension query.

    `shape` is the authoritative signal for which one applies -- mirrors CompiledQuery's own
    `result_shape` field (set from it directly in QueryExecutor.execute), rather than inferring
    shape from which of `value`/`rows` happens to be non-None. That inference used to be the only
    signal, and broke on a real, previously-undiscovered case (found via
    poc4_nl_chatbot/tests/test_real_olist_integration.py): a scalar GROWTH query whose comparison
    period matches zero rows produces a legitimately-NULL value (`NULLIF(0, 0)`'s division), which
    is a valid scalar result -- "undefined," not "absent, so this must actually be a grouped
    result" -- but the old presence-based invariant couldn't tell the difference and raised
    instead of returning `value=None`. `shape="scalar"` with `value=None` now means exactly that:
    a real, computed, mathematically-undefined answer.
    """

    metric_name: str
    shape: Literal["scalar", "grouped"]
    value: Optional[float] = None
    rows: Optional[list[dict[str, Any]]] = None
    metadata: QueryMetadata
    # Why this correct number may not mean what it appears to, stated by the engine itself (see
    # CaveatRule). Found on real Olist: repeat_purchase_rate was exactly 0 because every customer_id
    # there appears on one order, and a dashboard narrative turned that into a business conclusion.
    # LLM layers must not draw conclusions from a result that carries caveats.
    caveats: list[str] = Field(default_factory=list)
    # The registry's display format for this metric, stamped by AnalyticsEnginePipeline.run -- so a
    # renderer never has to guess whether 0.5 is a share or 790 an amount from the metric's name.
    format: Literal["money", "count", "percent", "number"] = "number"
    # A grouped result that filled its row limit and may have more groups than it returned. Anything
    # that treats `rows` as the complete set -- a share of the whole, a zero-filled comparison between
    # two periods -- must not, or it invents numbers for the groups that were cut.
    truncated: bool = False

    @model_validator(mode="after")
    def _fields_match_declared_shape(self) -> "MetricResult":
        if self.shape == "scalar":
            if self.rows is not None:
                raise ValueError('MetricResult shape="scalar" must not set `rows`')
        else:
            if self.value is not None:
                raise ValueError('MetricResult shape="grouped" must not set `value`')
            if self.rows is None:
                raise ValueError('MetricResult shape="grouped" requires `rows` (an empty list is fine)')
        return self
