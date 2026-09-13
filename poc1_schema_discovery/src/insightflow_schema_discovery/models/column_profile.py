from typing import Any, Optional

from pydantic import BaseModel


class ColumnProfile(BaseModel):
    """Deterministic metadata about a single column. This, not raw data, is what gets sent to the LLM."""

    column_name: str
    dtype_raw: str
    null_pct: float
    unique_count: int
    cardinality_ratio: float
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None
    mean: Optional[float] = None
    stdev: Optional[float] = None
    sample_values: list[Any] = []
    looks_like_id: bool = False
    looks_like_date: bool = False
    looks_like_categorical: bool = False
    looks_like_currency: bool = False
