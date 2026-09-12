"""What DashboardPlanner is allowed to see -- semantic model summary + live registry contents +
pre-computed signals, never raw source columns or unregistered fields (same information-hiding
discipline as POC 1's profiler-metadata-not-raw-data approach, and POC 2's AST never carrying raw
column names). Building this is DashboardGenerationPipeline's job (pipeline.py), not the
planner's own -- keeps DashboardPlanner a pure function of (context) -> DashboardSpec.
"""
from pydantic import BaseModel, Field

from insightflow.dashboard.signal import SignalResult
from insightflow.dashboard.spec import ComponentType


class MetricSummary(BaseModel):
    name: str
    kind: str
    description: str


class PlannerContext(BaseModel):
    entities: list[str]
    available_dimensions: list[str]
    available_metrics: list[MetricSummary] = Field(default_factory=list)
    signals: list[SignalResult] = Field(default_factory=list)
    supported_component_types: list[ComponentType] = Field(default_factory=list)
    # Found via the real Groq/real-Olist run (examples/real_olist_integration/README.md's second
    # "real bug found" entry): a subset of available_metrics' names, restricted to kind "measure"
    # or "base" -- the only kinds DashboardValidator._validate_group_by_supported_for_metric will
    # accept for a bar_chart/pie_chart/table. Precomputed here (same pattern as
    # supported_component_types) rather than making the planner infer the rule from each metric's
    # `kind` string in the prompt.
    groupable_metrics: list[str] = Field(default_factory=list)
    # Found via a third real Groq/real-Olist run (examples/real_olist_integration/README.md's
    # third "real bug found" entry): a subset of available_metrics' names excluding kind "growth"
    # -- DashboardDataResolver has no way to resolve a growth metric as ANY component (not just a
    # grouping one), so growth metrics must never be offered as a usable `metric_name` at all, even
    # though they're still listed informationally in available_metrics (they're what the
    # pre-computed growth signals below are named after).
    resolvable_metrics: list[str] = Field(default_factory=list)
