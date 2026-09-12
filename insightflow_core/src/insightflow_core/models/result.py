from typing import Any, Optional

from pydantic import BaseModel, model_validator


class QueryMetadata(BaseModel):
    sql: str
    execution_time_ms: float
    row_count: int


class MetricResult(BaseModel):
    """One typed shape for both scalar and grouped results (hld.md's resolved open question):
    `value` for a plain KPI, `rows` for a grouped/dimension query — mutually exclusive.
    """

    metric_name: str
    value: Optional[float] = None
    rows: Optional[list[dict[str, Any]]] = None
    metadata: QueryMetadata

    @model_validator(mode="after")
    def _exactly_one_of_value_or_rows(self) -> "MetricResult":
        if (self.value is None) == (self.rows is None):
            raise ValueError("MetricResult must set exactly one of `value` or `rows`, not both/neither")
        return self
