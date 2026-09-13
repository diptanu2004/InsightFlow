"""The Dashboard Spec DSL — planner output, pre-data. See docs/hld.md's "Dashboard Specification
DSL" section and docs/class_diagram.md's `DashboardSpec`/`ComponentSpec` sketch.

`ComponentType.LINE_CHART` is included for shape-completeness (`ComponentSpec.time_grain` only
means something for it) but is NOT buildable in POC 3 v1 — see class_diagram.md's banner note.
`DashboardValidator` rejects it outright; `PlannerContext.supported_component_types` (planner.py)
is what actually tells the LLM which types it may use, so this enum can safely list more than
v1 supports without the planner ever seeing LINE_CHART as an option.
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class ComponentType(str, Enum):
    KPI = "kpi"
    BAR_CHART = "bar_chart"
    PIE_CHART = "pie_chart"
    TABLE = "table"
    LINE_CHART = "line_chart"  # modeled, not yet resolvable -- see module docstring


# Component types whose AnalyticalQuery implies GROUP_BY (a dimension is required, and the
# underlying metric must be MetricKind.BASE per DashboardValidator.validate_group_by_supported_
# for_metric -- see class_diagram.md's note on POC 2 closure finding 9).
GROUPING_COMPONENT_TYPES = frozenset(
    {ComponentType.BAR_CHART, ComponentType.PIE_CHART, ComponentType.TABLE, ComponentType.LINE_CHART}
)


class FilterSpec(BaseModel):
    dimension: str
    label: str


class ComponentSpec(BaseModel):
    component_id: str
    type: ComponentType
    metric_name: str
    dimension: Optional[str] = None
    time_grain: Optional[str] = None
    rationale: str = ""

    @model_validator(mode="after")
    def _dimension_required_for_grouping_types(self) -> "ComponentSpec":
        if self.type in GROUPING_COMPONENT_TYPES and not self.dimension:
            raise ValueError(f"component type {self.type.value!r} requires a dimension")
        if self.type == ComponentType.KPI and self.dimension is not None:
            raise ValueError("KPI components must not set a dimension (they are a single scalar)")
        if self.time_grain is not None and self.type != ComponentType.LINE_CHART:
            raise ValueError("time_grain is only meaningful for LINE_CHART components")
        return self


class DashboardSpec(BaseModel):
    """Planner output, pre-validation, pre-data. Field names and structure per docs/hld.md's
    Output Contract section."""

    title: str
    narrative: str = ""
    filters: list[FilterSpec] = Field(default_factory=list)
    components: list[ComponentSpec] = Field(default_factory=list)
