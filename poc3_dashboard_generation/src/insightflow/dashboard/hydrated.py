"""POC 3's actual deliverable shape: a validated DashboardSpec with real MetricResults attached.

Deviates from docs/hld.md's original sketch (`results: dict[component_id, MetricResult]`) on
purpose -- see docs/class_diagram.md's Notes: a list of (spec, result) pairs preserves the
planner's intended display order without relying on dict-insertion-order as a semantic guarantee
across a JSON serialization boundary, and each pair already carries its own id via
`spec.component_id`.
"""
from pydantic import BaseModel, Field

from insightflow.dashboard.spec import ComponentSpec, FilterSpec
from insightflow_core.models import MetricResult


class HydratedComponent(BaseModel):
    spec: ComponentSpec
    result: MetricResult


class HydratedDashboard(BaseModel):
    title: str
    narrative: str = ""
    filters: list[FilterSpec] = Field(default_factory=list)
    components: list[HydratedComponent] = Field(default_factory=list)
