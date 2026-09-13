"""DashboardValidator -- deterministic, spec-level checks that run BEFORE a single component is
resolved against POC 2's engine. See docs/class_diagram.md's Notes for why this duplicates some
of what POC 2's own ASTValidator will check again per-component anyway: intentional double
checking, the same reasoning POC 2 gives for SQLSafetyChecker existing independently of
ASTValidator. Failing the whole spec up front, with every problem component named, is a better
failure mode for a dashboard than resolving components one at a time and surfacing whichever
broken one happens to run first.
"""
from insightflow_dashboard.dashboard.spec import GROUPING_COMPONENT_TYPES, ComponentSpec, ComponentType, DashboardSpec
from insightflow_dashboard.dashboard.validation_types import DashboardValidationError, DashboardValidationResult
from insightflow_core.compilation.field_resolver import FieldResolver
from insightflow_core.models.registry import MetricKind
from insightflow_core.validation.metric_resolvability import group_by_problem, unresolvable_reason
from insightflow_dashboard.registry import MetricRegistry

# v1 only resolves these component types (docs/hld.md's "In scope" section, docs/class_diagram.md's
# banner note on LINE_CHART). Kept as a module constant, not hardcoded inline, so it's the one
# place to change when POC 2 eventually gains time-bucketed grouping.
SUPPORTED_COMPONENT_TYPES = frozenset(
    {ComponentType.KPI, ComponentType.BAR_CHART, ComponentType.PIE_CHART, ComponentType.TABLE}
)


class DashboardValidator:
    def __init__(
        self,
        registry: MetricRegistry,
        field_resolver: FieldResolver,
        min_components: int,
        max_components: int,
    ):
        self.registry = registry
        self.field_resolver = field_resolver
        self.min_components = min_components
        self.max_components = max_components

    def validate(self, spec: DashboardSpec) -> DashboardValidationResult:
        errors: list[DashboardValidationError] = []
        errors += self._validate_component_count(spec.components)
        errors += self._validate_no_duplicate_kpis(spec.components)
        errors += self._validate_filters(spec.filters)
        for component in spec.components:
            errors += self._validate_component(component)
        return DashboardValidationResult(is_valid=not errors, errors=errors)

    # -- per-component checks -------------------------------------------------------------

    def _validate_component(self, component: ComponentSpec) -> list[DashboardValidationError]:
        errors = self._validate_component_type_supported(component)
        errors += self._validate_metric_registered(component)
        if component.dimension is not None:
            errors += self._validate_dimension_exists(component)
        # Only meaningful once the metric is known to be registered -- avoids a confusing second
        # error (or an AttributeError from registry.resolve on an unknown name) piled onto an
        # already-reported unknown_metric.
        if not any(e.code == "unknown_metric" and e.component_id == component.component_id for e in errors):
            errors += self._validate_metric_resolves_on_dataset(component)
            errors += self._validate_metric_directly_resolvable(component)
            errors += self._validate_group_by_supported_for_metric(component)
        # Last, and only once everything else about this component checked out, so the one thing
        # left for group_by_problem to report is the join itself rather than a repeat of an earlier
        # error. The planner is only offered joinable pairs; this catches one it invented anyway.
        if component.type in GROUPING_COMPONENT_TYPES and component.dimension is not None and not errors:
            problem = group_by_problem(
                component.metric_name, component.dimension, self.registry, self.field_resolver.semantic_model
            )
            if problem is not None:
                errors.append(
                    DashboardValidationError(code="no_join_path", message=problem, component_id=component.component_id)
                )
        return errors

    def _validate_metric_resolves_on_dataset(self, component: ComponentSpec) -> list[DashboardValidationError]:
        # The pipeline already withholds unresolvable metrics from the planner; this re-checks the
        # spec it got back, same double-checking as every other planner-context filter here. A
        # hallucinated-but-registered metric the dataset can't compute must be rejected with the
        # component named, not crash inside the engine's FieldResolver.
        reason = unresolvable_reason(component.metric_name, self.registry, self.field_resolver.semantic_model)
        if reason is None:
            return []
        return [
            DashboardValidationError(code="unresolvable_metric", message=reason, component_id=component.component_id)
        ]

    def _validate_metric_directly_resolvable(self, component: ComponentSpec) -> list[DashboardValidationError]:
        # Real bug found via the real Groq/real-Olist run: DashboardDataResolver.build_query()
        # only ever emits an AGGREGATE or GROUP_BY AnalyticalQuery -- never GROWTH, since a GROWTH
        # query needs an explicit comparison-period pair (a GrowthSpec), a business decision
        # DashboardDataResolver has no way to make for an arbitrary planner-chosen component
        # (SignalGatherer makes that same decision, but only for its own fixed, pre-planning
        # diagnostic signals -- see its reference_date). Before this check, a GROWTH-kind metric
        # picked for ANY component (not just a grouping one -- _validate_group_by_supported_for_metric
        # below only covers grouping types) sailed through validation and crashed deep inside
        # SQLCompiler._compile_growth ("GROWTH compilation requires query.growth") instead of
        # failing cleanly here.
        resolved = self.registry.resolve(component.metric_name)
        kind = getattr(resolved, "kind", None)  # None for a bare Measure -- always resolvable
        if kind == MetricKind.GROWTH:
            return [
                DashboardValidationError(
                    code="growth_metric_not_directly_resolvable",
                    message=(
                        f'metric "{component.metric_name}" is kind=growth; DashboardDataResolver '
                        "has no way to supply the comparison-period pair a growth query needs, so "
                        "a growth metric can only appear as a pre-computed diagnostic signal, "
                        "never as a component's metric_name"
                    ),
                    component_id=component.component_id,
                )
            ]
        return []

    def _validate_component_type_supported(self, component: ComponentSpec) -> list[DashboardValidationError]:
        if component.type not in SUPPORTED_COMPONENT_TYPES:
            return [
                DashboardValidationError(
                    code="component_type_not_yet_supported",
                    message=(
                        f'component type "{component.type.value}" has no backing metric primitive '
                        "in POC 2 yet (see docs/class_diagram.md's banner note) -- supported types "
                        f"are {sorted(t.value for t in SUPPORTED_COMPONENT_TYPES)}"
                    ),
                    component_id=component.component_id,
                )
            ]
        return []

    def _validate_metric_registered(self, component: ComponentSpec) -> list[DashboardValidationError]:
        if not self.registry.is_registered(component.metric_name):
            return [
                DashboardValidationError(
                    code="unknown_metric",
                    message=f'"{component.metric_name}" is not a registered measure or metric',
                    component_id=component.component_id,
                )
            ]
        return []

    def _validate_dimension_exists(self, component: ComponentSpec) -> list[DashboardValidationError]:
        try:
            self.field_resolver.find_entity_for_field(component.dimension)
        except KeyError:
            return [
                DashboardValidationError(
                    code="unknown_dimension",
                    message=f'"{component.dimension}" is not a known field in the semantic model',
                    component_id=component.component_id,
                )
            ]
        except ValueError as exc:
            return [
                DashboardValidationError(
                    code="ambiguous_dimension",
                    message=str(exc),
                    component_id=component.component_id,
                )
            ]
        return []

    def _validate_group_by_supported_for_metric(self, component: ComponentSpec) -> list[DashboardValidationError]:
        # A real POC 2 closure bug, not speculative caution: GROUP_BY against a non-BASE metric
        # (a RATIO like "aov") used to compile fine and silently return the wrong shape/number
        # (poc2_analytics_engine/docs/class_diagram.md, closure finding 9). Any component type in
        # GROUPING_COMPONENT_TYPES implies GROUP_BY once DashboardDataResolver builds its AST, so
        # this is checked here -- at spec/plan time, with the component named -- rather than only
        # relying on POC 2's own ASTValidator to reject it per-component later.
        if component.type not in GROUPING_COMPONENT_TYPES:
            return []
        resolved = self.registry.resolve(component.metric_name)
        kind = getattr(resolved, "kind", None)  # None for a bare Measure -- always BASE-shaped
        if kind is not None and kind != MetricKind.BASE:
            return [
                DashboardValidationError(
                    code="group_by_unsupported_for_metric_kind",
                    message=(
                        f'metric "{component.metric_name}" is kind={kind.value}; a '
                        f'{component.type.value} component needs a BASE-kind metric or bare '
                        "measure to group by a dimension (RATIO/GROWTH/HAVING_RATIO grouping is "
                        "not implemented in POC 2 -- see class_diagram.md)"
                    ),
                    component_id=component.component_id,
                )
            ]
        return []

    # -- spec-level checks ------------------------------------------------------------------

    def _validate_no_duplicate_kpis(self, components: list[ComponentSpec]) -> list[DashboardValidationError]:
        seen: set[str] = set()
        errors: list[DashboardValidationError] = []
        for component in components:
            if component.type != ComponentType.KPI:
                continue
            if component.metric_name in seen:
                errors.append(
                    DashboardValidationError(
                        code="duplicate_kpi",
                        message=f'metric "{component.metric_name}" appears as more than one KPI component',
                        component_id=component.component_id,
                    )
                )
            seen.add(component.metric_name)
        return errors

    def _validate_component_count(self, components: list[ComponentSpec]) -> list[DashboardValidationError]:
        count = len(components)
        if count < self.min_components:
            return [
                DashboardValidationError(
                    code="too_few_components",
                    message=f"dashboard has {count} components, fewer than the minimum {self.min_components}",
                )
            ]
        if count > self.max_components:
            return [
                DashboardValidationError(
                    code="too_many_components",
                    message=f"dashboard has {count} components, more than the maximum {self.max_components}",
                )
            ]
        return []

    def _validate_filters(self, filters) -> list[DashboardValidationError]:
        errors: list[DashboardValidationError] = []
        for f in filters:
            try:
                self.field_resolver.find_entity_for_field(f.dimension)
            except KeyError:
                errors.append(
                    DashboardValidationError(
                        code="unknown_filter_dimension",
                        message=f'filter dimension "{f.dimension}" is not a known field in the semantic model',
                    )
                )
            except ValueError as exc:
                errors.append(DashboardValidationError(code="ambiguous_filter_dimension", message=str(exc)))
        return errors
