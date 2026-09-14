"""DashboardDataResolver -- turns a VALIDATED DashboardSpec into a HydratedDashboard by building
one AnalyticalQuery per component and running it through POC 2's unmodified AnalyticsEnginePipeline
-- the exact same engine SignalGatherer already used for pre-planning signals. No second
arithmetic path exists anywhere in POC 3 (architecture doc §2.1, §32.1).
"""
from insightflow_dashboard.dashboard.hydrated import HydratedComponent, HydratedDashboard
from insightflow_dashboard.dashboard.spec import ComponentSpec, ComponentType, DashboardSpec, GROUPING_COMPONENT_TYPES
from insightflow_core.models import AnalyticalQuery, OperationType, SortSpec
from insightflow_core.pipeline import AnalyticsEnginePipeline

# ComponentSpec doesn't (yet) let the planner specify a sort/row-limit for a grouped chart --
# that's a display concern the planner shouldn't need to reason about. These are reasonable v1
# defaults for a chart, not planner output: highest-value groups first, capped at a chart-sized
# row count rather than the engine's full max_row_limit.
DEFAULT_CHART_ROW_LIMIT = 20


class DashboardDataResolver:
    def __init__(self, engine: AnalyticsEnginePipeline):
        self.engine = engine

    def resolve(self, spec: DashboardSpec) -> HydratedDashboard:
        components: list[HydratedComponent] = []
        for component in spec.components:
            query = self.build_query(component)
            result = self.engine.run(query)
            components.append(HydratedComponent(spec=component, result=result))
        return HydratedDashboard(title=spec.title, narrative=spec.narrative, filters=spec.filters, components=components)

    def build_query(self, component: ComponentSpec) -> AnalyticalQuery:
        if component.type in GROUPING_COMPONENT_TYPES:
            return AnalyticalQuery(
                operation=OperationType.GROUP_BY,
                metric=component.metric_name,
                dimension=component.dimension,
                sort=SortSpec(field="value", direction="desc"),
                # A pie is part-to-whole, so it needs the whole: capped at the top 20, real Olist
                # showed "SP 42.5%" as SP's share of the top 20 states' customers (99,441 total, 27
                # states) and silently dropped the other 7. Pies get every group (the engine's own
                # row cap still applies); the renderer folds the tail into "Other". Bars and tables
                # rank, so a top-N cut is honest there -- and the renderer labels it.
                limit=None if component.type == ComponentType.PIE_CHART else DEFAULT_CHART_ROW_LIMIT,
            )
        return AnalyticalQuery(operation=OperationType.AGGREGATE, metric=component.metric_name)
