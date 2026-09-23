"""The AST. See docs/class_diagram.md and docs/hld.md's "Settled before implementation" section
for the resolved design decisions encoded here:
  - one AST = one metric (no multi-metric queries)
  - time_filter is always explicit start_date/end_date, never a relative-range string
  - having_override, when supplied, takes precedence over the metric's registry default
  - filters restrict rows to listed values of a dimension (Phase 8): values are literals, never SQL
"""
from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class OperationType(str, Enum):
    AGGREGATE = "aggregate"
    GROUP_BY = "group_by"
    GROWTH = "growth"


class TimeFilter(BaseModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _end_not_before_start(self) -> "TimeFilter":
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self


class SortSpec(BaseModel):
    field: str
    direction: str = "desc"

    @model_validator(mode="after")
    def _direction_is_valid(self) -> "SortSpec":
        if self.direction not in ("asc", "desc"):
            raise ValueError(f"direction must be 'asc' or 'desc', got {self.direction!r}")
        return self


class GrowthSpec(BaseModel):
    current_period: TimeFilter
    comparison_period: TimeFilter


class HavingClause(BaseModel):
    field: str
    operator: str
    value: float

    @model_validator(mode="after")
    def _operator_is_valid(self) -> "HavingClause":
        allowed = {">=", "<=", "=", "!=", ">", "<"}
        if self.operator not in allowed:
            raise ValueError(f"operator must be one of {sorted(allowed)}, got {self.operator!r}")
        return self


MAX_FILTER_VALUES = 50
_MAX_FILTER_VALUE_LENGTH = 200


def normalize_filter_value(value: str) -> str:
    """How a filter value is compared: trimmed and case-insensitive, on both sides (SQLCompiler lowers the
    column the same way), so "electronics" matches "Electronics " in the data."""
    return value.strip().lower()


class ValueFilter(BaseModel):
    """Keep only rows whose `dimension` equals one of `values` -- "customers in AL, RN and CE".

    Added in Phase 8: without it chat couldn't answer such a question, and an explanation LLM summed three
    verified per-region counts itself. Every value is a bound parameter; the dimension is checked against the
    semantic model like any other dimension (ASTValidator).
    """

    dimension: str
    values: list[str] = Field(min_length=1, max_length=MAX_FILTER_VALUES)

    @field_validator("values")
    @classmethod
    def _values_are_usable(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not value.strip():
                raise ValueError("filter values must not be blank")
            if len(value) > _MAX_FILTER_VALUE_LENGTH:
                raise ValueError(f"filter values must be at most {_MAX_FILTER_VALUE_LENGTH} characters")
            key = normalize_filter_value(value)
            if key not in seen:
                seen.add(key)
                cleaned.append(value.strip())
        return cleaned


class AnalyticalQuery(BaseModel):
    """The Analytical Query / AST — see architecture doc §10 and class_diagram.md."""

    operation: OperationType
    metric: str
    dimension: Optional[str] = None
    time_filter: Optional[TimeFilter] = None
    growth: Optional[GrowthSpec] = None
    having_override: Optional[HavingClause] = None
    sort: Optional[SortSpec] = None
    limit: Optional[int] = None
    filters: list[ValueFilter] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape_matches_operation(self) -> "AnalyticalQuery":
        dimensions = [f.dimension for f in self.filters]
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("each dimension may appear in `filters` at most once; list all its values in one filter")
        if self.operation == OperationType.GROWTH:
            if self.growth is None:
                raise ValueError("operation=GROWTH requires `growth` to be set")
            if self.time_filter is not None:
                raise ValueError("operation=GROWTH uses `growth`'s two periods, not `time_filter`")
        else:
            if self.growth is not None:
                raise ValueError("`growth` is only valid when operation=GROWTH")
        if self.operation != OperationType.GROUP_BY and self.dimension is not None:
            raise ValueError("`dimension` is only valid when operation=GROUP_BY")
        return self
