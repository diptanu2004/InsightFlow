"""SignalGatherer -- runs a small fixed set of diagnostic queries through POC 2's unmodified
engine BEFORE planning, so DashboardPlanner reacts to real numbers (architecture doc §13.2:
"revenue declining -> emphasize revenue trend + category contribution") instead of guessing
relevance from field names alone. See docs/hld.md's "Data-aware prioritization" section and
docs/class_diagram.md's Notes.

Found while implementing this class (documented here, not just in a code review comment, per
project convention of flagging real gaps where they're found): growth signals need two calendar
windows to compare, and POC 2 has no way to ask "what is the latest date in this dataset" --
there is no registered MAX(date) measure/operator. A real deployment should derive these windows
from the dataset's own date range, not wall-clock "today"; that capability doesn't exist yet, so
`reference_date` defaults to `date.today()` but is an explicit constructor parameter specifically
so callers (tests, and a future real MAX(date) capability) can override it. Against this POC's own
static, historically-dated sample dataset (Nov 2025 - Feb 2026), a wall-clock "today" reference in
2026+ puts the comparison window outside the data entirely, making a growth ratio's denominator
zero.

That used to ALSO trip `insightflow_core`'s own now-fixed gap (a genuinely-undefined scalar had no
representation and crashed `MetricResult` instead of returning `value=None` cleanly -- fixed via
`MetricResult.shape`, found and closed via `poc4_nl_chatbot`'s real-Olist integration test). Now
that an undefined scalar comes back cleanly as `MetricResult(shape="scalar", value=None)` instead
of raising, `gather()` below explicitly checks for that case (not just catching an exception) and
treats it the same as any other infeasible signal -- a validation rejection (e.g.
growth_entity_missing_time_field), a crash on execution (still possible for other, unrelated
reasons), OR a clean-but-undefined scalar result -- and omits it, exactly per hld.md's
"SignalGatherer must handle a rejected signal gracefully" note. The planner sees a shorter, honest
signal list rather than being shown a KPI whose value is `None`.
"""
from datetime import date, timedelta
from typing import Optional

from pydantic import BaseModel

from insightflow_core.compilation.field_resolver import FieldResolver
from insightflow_core.models import AnalyticalQuery, GrowthSpec, MetricResult, OperationType, SortSpec, TimeFilter
from insightflow_core.models.semantic_model import SemanticModel
from insightflow_core.pipeline import AnalyticsEnginePipeline
from insightflow_dashboard.registry import MetricRegistry

_GROWTH_WINDOW_DAYS = 90

# (signal_name, metric_name) for the three base KPIs -- always attempted, they need no dimension.
_BASE_KPI_SIGNALS = (
    ("total_revenue", "revenue"),
    ("total_orders", "orders"),
    ("total_customers", "customers"),
)

# (signal_name, metric_name) for composite KPIs. Phase 8: the planner used to write a narrative about
# these without ever seeing their values -- on real Olist it declared "the low repeat purchase rate
# reveals growth is driven by new customers" about a rate it never computed (and that was 0 only by
# construction). Computing them here lets the narrative be grounded in real values and their caveats.
_COMPOSITE_KPI_SIGNALS = (
    ("average_order_value", "aov"),
    ("repeat_purchase_rate", "repeat_purchase_rate"),
)

# (signal_name, growth_metric_name) -- attempted if the growth metric is registered.
_GROWTH_SIGNALS = (
    ("revenue_growth", "revenue_growth"),
    ("order_growth", "order_growth"),
    ("customer_growth", "customer_growth"),
)

# (signal_name_prefix, dimension) -- attempted only if the dimension actually resolves against
# the semantic model (a dataset may not have a "region" or "category" at all).
_DIMENSION_SIGNALS = (
    ("category", "category"),
    ("region", "region"),
)


class SignalDefinition(BaseModel):
    name: str
    query: AnalyticalQuery


class SignalResult(BaseModel):
    name: str
    result: MetricResult


class SignalGatherer:
    def __init__(
        self,
        engine: AnalyticsEnginePipeline,
        semantic_model: SemanticModel,
        registry: MetricRegistry,
        reference_date: Optional[date] = None,
    ):
        self.engine = engine
        self.semantic_model = semantic_model
        self.registry = registry
        self.field_resolver = FieldResolver(semantic_model)
        # No wall-clock fallback: growth windows are measured back from this date, and a dataset
        # that ends in 2018 measured from today has empty windows, so growth signals silently
        # vanished for every real upload built through build_dashboard_pipeline (found in Phase 8).
        # None means "no data-derived reference date" -- growth signals are skipped, not guessed.
        # Same rule POC 4 settled on for chat's date bounds.
        self.reference_date = reference_date

    def gather(self) -> list[SignalResult]:
        results: list[SignalResult] = []
        for signal in self.fixed_signals():
            try:
                result = self.engine.run(signal.query)
            except Exception:
                # Infeasible for this dataset (missing time field, unregistered metric, etc.) --
                # omit, don't crash the whole generation run. See module docstring for the
                # concrete gaps this is guarding against.
                continue
            if result.shape == "scalar" and result.value is None:
                # A clean, non-crashing but mathematically-undefined scalar (e.g. a growth ratio
                # whose comparison period matches zero rows) -- see module docstring. Just as
                # infeasible for the planner's purposes as an outright rejection; omit rather
                # than showing a KPI whose value is None.
                continue
            results.append(SignalResult(name=signal.name, result=result))
        return results

    def fixed_signals(self) -> list[SignalDefinition]:
        signals: list[SignalDefinition] = []

        for name, metric in _BASE_KPI_SIGNALS + _COMPOSITE_KPI_SIGNALS:
            if self.registry.is_registered(metric):
                signals.append(SignalDefinition(name=name, query=AnalyticalQuery(operation=OperationType.AGGREGATE, metric=metric)))

        growth_signals = _GROWTH_SIGNALS if self.reference_date is not None else ()
        current, comparison = self._growth_windows() if growth_signals else (None, None)
        for name, metric in growth_signals:
            if self.registry.is_registered(metric):
                signals.append(
                    SignalDefinition(
                        name=name,
                        query=AnalyticalQuery(
                            operation=OperationType.GROWTH,
                            metric=metric,
                            growth=GrowthSpec(current_period=current, comparison_period=comparison),
                        ),
                    )
                )

        if self.registry.is_registered("revenue"):
            signals.extend(self._build_dimension_signals())

        return signals

    def _build_dimension_signals(self) -> list[SignalDefinition]:
        signals: list[SignalDefinition] = []
        for label, dimension in _DIMENSION_SIGNALS:
            if not self._dimension_exists(dimension):
                continue
            for direction, suffix in (("desc", "top"), ("asc", "bottom")):
                signals.append(
                    SignalDefinition(
                        name=f"{suffix}_{label}_by_revenue",
                        query=AnalyticalQuery(
                            operation=OperationType.GROUP_BY,
                            metric="revenue",
                            dimension=dimension,
                            sort=SortSpec(field="value", direction=direction),
                            limit=1,
                        ),
                    )
                )
        return signals

    def _dimension_exists(self, dimension: str) -> bool:
        try:
            self.field_resolver.find_entity_for_field(dimension)
        except (KeyError, ValueError):
            return False
        return True

    def _growth_windows(self) -> tuple[TimeFilter, TimeFilter]:
        current_end = self.reference_date
        current_start = current_end - timedelta(days=_GROWTH_WINDOW_DAYS - 1)
        comparison_end = current_start - timedelta(days=1)
        comparison_start = comparison_end - timedelta(days=_GROWTH_WINDOW_DAYS - 1)
        return (
            TimeFilter(start_date=current_start, end_date=current_end),
            TimeFilter(start_date=comparison_start, end_date=comparison_end),
        )
