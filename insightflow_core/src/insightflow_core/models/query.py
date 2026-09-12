"""The AST. See docs/class_diagram.md and docs/hld.md's "Settled before implementation" section
for the resolved design decisions encoded here:
  - one AST = one metric (no multi-metric queries)
  - time_filter is always explicit start_date/end_date, never a relative-range string
  - having_override, when supplied, takes precedence over the metric's registry default
"""
from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, model_validator


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

    @model_validator(mode="after")
    def _shape_matches_operation(self) -> "AnalyticalQuery":
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
