from insightflow_core.compilation.sql_compiler import SQLCompiler
from insightflow_core.models import AnalyticalQuery, OperationType, TimeFilter, ValidationError, ValidationResult
from insightflow_core.models.query import HavingClause
from insightflow_core.models.registry import MetricKind
from insightflow_core.models.semantic_model import SemanticModel
from insightflow_core.registry import MetricRegistry

# Sanity bound against absurd ranges (e.g. a typo'd year). TimeFilter's own validator already
# enforces start_date <= end_date; this is a *semantic* check on top of that structural one.
_MAX_TIME_FILTER_DAYS = 366 * 20


class ASTValidator:
    """Checks the AST against the MetricRegistry and SemanticModel *before* anything is
    compiled to SQL — see hld.md's "Why validation happens before compilation, not after."
    Structural validity (types, required fields per OperationType) is already enforced by
    AnalyticalQuery's own pydantic validators; this class checks *semantic* validity — does the
    referenced metric/dimension actually exist, resolvable against this Semantic Model.
    """

    def __init__(self, registry: MetricRegistry, semantic_model: SemanticModel, max_row_limit: int):
        self.registry = registry
        self.semantic_model = semantic_model
        self.max_row_limit = max_row_limit

    def validate(self, query: AnalyticalQuery) -> ValidationResult:
        errors: list[ValidationError] = []
        errors += self._validate_metric(query.metric)
        if query.dimension is not None:
            errors += self._validate_dimension(query.dimension)
        if query.operation == OperationType.GROUP_BY and query.dimension is not None:
            errors += self._validate_group_by_supported(query.metric)
        if query.time_filter is not None:
            errors += self._validate_time_filter(query.time_filter)
            errors += self._validate_time_filter_supported(query.metric)
            errors += self._validate_time_filter_entity_has_time_field(query.metric)
        if query.growth is not None:
            errors += self._validate_growth(query)
        if query.having_override is not None:
            errors += self._validate_having_override(query.having_override)
        errors += self._validate_row_limit(query)
        return ValidationResult(is_valid=not errors, errors=errors)

    def _validate_metric(self, metric: str) -> list[ValidationError]:
        if not self.registry.is_registered(metric):
            return [
                ValidationError(
                    code="unknown_metric",
                    message=f'"{metric}" is not a registered measure or metric',
                    field="metric",
                )
            ]
        return []

    def _validate_dimension(self, dimension: str) -> list[ValidationError]:
        # Checks existence AND entity-level ambiguity — found via the real-dataset integration
        # test: the original version only checked "does this field exist somewhere," which let
        # a genuinely ambiguous dimension (e.g. Olist's "category," mapped into both `products`
        # and `category_translation`; "region," mapped into both `customers` and `sellers`) pass
        # validation and then blow up as an unhandled ValueError deep inside
        # SQLCompiler/FieldResolver.find_entity_for_field instead of failing cleanly here.
        matching_entities = [e.name for e in self.semantic_model.entities if any(f.name == dimension for f in e.fields)]
        if not matching_entities:
            return [
                ValidationError(
                    code="unknown_dimension",
                    message=f'"{dimension}" is not a known field in the semantic model',
                    field="dimension",
                )
            ]
        if len(matching_entities) > 1:
            return [
                ValidationError(
                    code="ambiguous_dimension",
                    message=(
                        f'"{dimension}" exists in more than one entity ({matching_entities}) and '
                        "cannot be used as a dimension without disambiguation"
                    ),
                    field="dimension",
                )
            ]
        return []

    def _validate_group_by_supported(self, metric: str) -> list[ValidationError]:
        # Found during the POC 2 closure sweep, not by any earlier real-data test: only
        # `SQLCompiler._compile_base` (a bare Measure, or a BASE-kind MetricDefinition) applies
        # `query.dimension` at all -- `_compile_ratio`/`_compile_growth`/`_compile_having_ratio`
        # never call `_apply_dimension`, so a GROUP_BY query against e.g. "aov" (RATIO) doesn't
        # error, it *silently ignores the dimension* and returns the whole-dataset scalar wrapped
        # as if it were a one-row grouped result (`rows == [{"value": ...}]`, no dimension column
        # at all) -- a caller expecting one row per group gets a wrong answer with no error. Not
        # registered/resolvable is already reported by `_validate_metric`, so this only adds a
        # new error for a metric that resolves fine but to an unsupported kind, rather than
        # duplicating that check.
        if not self.registry.is_registered(metric):
            return []
        resolved = self.registry.resolve(metric)
        kind = getattr(resolved, "kind", None)  # None for a bare Measure -- always BASE-shaped
        if kind is not None and kind != MetricKind.BASE:
            return [
                ValidationError(
                    code="group_by_unsupported_for_metric_kind",
                    message=(
                        f'metric "{metric}" is kind={kind.value}; GROUP BY is only supported for '
                        "a bare measure or a BASE metric in POC 2 (RATIO/GROWTH/HAVING_RATIO "
                        "grouping is not implemented, not just unvalidated -- see class_diagram.md)"
                    ),
                    field="dimension",
                )
            ]
        return []

    def _validate_time_filter_supported(self, metric: str) -> list[ValidationError]:
        # Same class of gap as _validate_group_by_supported, found the same way (closure sweep,
        # not an earlier real-data test -- no fixture or example ever combined a time_filter with
        # a HAVING_RATIO metric). `_compile_having_ratio` never calls `_apply_time_filter` or
        # threads `query.time_filter` into its CTE at all, so a time-filtered
        # "repeat_purchase_rate" used to compile fine and silently return the UNFILTERED,
        # whole-dataset answer -- a wrong number with no error, not a rejection. (RATIO already
        # applies time_filter correctly via _compile_ratio's independent subqueries; GROWTH
        # can't combine with time_filter at all -- AnalyticalQuery's own validator already
        # forbids setting both -- so HAVING_RATIO is the only kind with this gap.)
        if not self.registry.is_registered(metric):
            return []
        resolved = self.registry.resolve(metric)
        kind = getattr(resolved, "kind", None)
        if kind == MetricKind.HAVING_RATIO:
            return [
                ValidationError(
                    code="time_filter_unsupported_for_metric_kind",
                    message=(
                        f'metric "{metric}" is kind=having_ratio; time_filter is not supported for '
                        "HAVING_RATIO metrics in POC 2 (not just unvalidated -- see class_diagram.md)"
                    ),
                    field="time_filter",
                )
            ]
        return []

    def _validate_time_filter_entity_has_time_field(self, metric: str) -> list[ValidationError]:
        # Found via POC 4's real-Olist integration test (poc4_nl_chatbot/tests/
        # test_real_olist_integration.py): an AGGREGATE query for "revenue" (a bare measure on
        # the `payments` entity, which has no TIME_FIELD column) with an explicit time_filter
        # compiled fine as far as this validator was concerned, then crashed with an unhandled
        # KeyError deep inside SQLCompiler/FieldResolver -- _validate_growth already has this
        # exact check for GROWTH queries (growth_entity_missing_time_field), but it was never
        # generalized to plain AGGREGATE/GROUP_BY + time_filter, which hits the same
        # _apply_time_filter code path via a different metric kind. Mirrors
        # _validate_group_by_supported/_validate_time_filter_supported's own reasoning: a
        # gap that "should never happen" until a real dataset's entity shape finds it.
        if not self.registry.is_registered(metric):
            return []
        resolved = self.registry.resolve(metric)
        kind = getattr(resolved, "kind", None)

        entities: list[str] = []
        if kind is None:
            # A bare Measure, always BASE-shaped.
            entities = [resolved.entity]
        elif kind == MetricKind.BASE:
            base = self.registry.resolve(resolved.base_measure)
            entities = [base.entity]
        elif kind == MetricKind.RATIO:
            numerator = self.registry.resolve(resolved.numerator_measure)
            denominator = self.registry.resolve(resolved.denominator_measure)
            entities = [numerator.entity, denominator.entity]
        # HAVING_RATIO is already rejected outright by _validate_time_filter_supported above;
        # GROWTH can never carry a time_filter at all (AnalyticalQuery's own validator forbids
        # setting both `time_filter` and `growth`) -- neither needs a check here.

        missing = [
            e
            for e in entities
            if not any(ent.name == e and any(f.name == SQLCompiler.TIME_FIELD for f in ent.fields) for ent in self.semantic_model.entities)
        ]
        if missing:
            return [
                ValidationError(
                    code="time_filter_entity_missing_time_field",
                    message=(
                        f'metric "{metric}" resolves to entit{"y" if len(missing) == 1 else "ies"} '
                        f'{missing}, which ha{"s" if len(missing) == 1 else "ve"} no '
                        f'"{SQLCompiler.TIME_FIELD}" field to filter on'
                    ),
                    field="time_filter",
                )
            ]
        return []

    def _validate_time_filter(self, tf: TimeFilter) -> list[ValidationError]:
        if (tf.end_date - tf.start_date).days > _MAX_TIME_FILTER_DAYS:
            return [
                ValidationError(
                    code="time_filter_range_too_large",
                    message=f"time_filter spans more than {_MAX_TIME_FILTER_DAYS} days",
                    field="time_filter",
                )
            ]
        return []

    def _validate_growth(self, query: AnalyticalQuery) -> list[ValidationError]:
        growth = query.growth
        errors = self._validate_time_filter(growth.current_period) + self._validate_time_filter(
            growth.comparison_period
        )
        overlaps = (
            growth.current_period.start_date <= growth.comparison_period.end_date
            and growth.comparison_period.start_date <= growth.current_period.end_date
        )
        if overlaps:
            errors.append(
                ValidationError(
                    code="growth_periods_overlap",
                    message="growth.current_period and growth.comparison_period must not overlap",
                    field="growth",
                )
            )

        # Found via the real Olist integration test: a GROWTH metric's base measure has to live
        # on an entity that actually carries SQLCompiler.TIME_FIELD, or period filtering has
        # nothing to filter on. "revenue" resolved (by registry configuration choice) to the
        # `payments` entity, which has no date column at all in the real Olist schema — without
        # this check, that surfaced as an unhandled KeyError deep inside
        # SQLCompiler/FieldResolver instead of a clean rejection here, the same class of gap the
        # ambiguous-dimension check above closes for dimensions.
        if self.registry.is_registered(query.metric):
            resolved = self.registry.resolve(query.metric)
            base_measure_name = getattr(resolved, "base_measure", None)
            if base_measure_name and self.registry.is_registered(base_measure_name):
                measure = self.registry.resolve(base_measure_name)
                entity_name = getattr(measure, "entity", None)
                has_time_field = entity_name is not None and any(
                    e.name == entity_name and any(f.name == SQLCompiler.TIME_FIELD for f in e.fields)
                    for e in self.semantic_model.entities
                )
                if entity_name is not None and not has_time_field:
                    errors.append(
                        ValidationError(
                            code="growth_entity_missing_time_field",
                            message=(
                                f'metric "{query.metric}"\'s base measure lives on entity '
                                f'"{entity_name}", which has no "{SQLCompiler.TIME_FIELD}" field '
                                "to filter growth periods on"
                            ),
                            field="growth",
                        )
                    )
        return errors

    def _validate_having_override(self, having: HavingClause) -> list[ValidationError]:
        # `having.field` names a registered measure whose per-group aggregate is thresholded
        # (see SQLCompiler._compile_having_ratio), not a raw semantic-model field — same checks
        # the registry's own having-clause defaults are trusted to satisfy.
        if not self.registry.is_registered(having.field):
            return [
                ValidationError(
                    code="unknown_having_field",
                    message=f'having_override.field "{having.field}" is not a registered measure or metric',
                    field="having_override",
                )
            ]
        return []

    def _validate_row_limit(self, query: AnalyticalQuery) -> list[ValidationError]:
        if query.limit is not None and query.limit > self.max_row_limit:
            return [
                ValidationError(
                    code="row_limit_exceeded",
                    message=f"limit {query.limit} exceeds max_row_limit {self.max_row_limit}",
                    field="limit",
                )
            ]
        return []
