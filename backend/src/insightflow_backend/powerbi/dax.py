"""Registry metric -> DAX, generated from the same bindings the SQL compiler uses.

This is the file where Phase 9's central risk lives: a DAX measure is a second deterministic
implementation of a formula insightflow_core already implements in SQL, and the two can disagree
silently. Two things keep that manageable, and neither is "be careful":

  1. Nothing here is hand-written per metric. Every measure is derived from the registry through
     `bind_metric` -- the *same* call `SQLCompiler.compile` makes -- so which entity a measure reads
     and which physical column it aggregates can't drift from the engine, whatever the dataset.
     A metric the engine would refuse for this dataset is refused here too, by the same exception.
  2. Each translation below states the SQL it mirrors. Where DAX cannot match SQL exactly, the
     difference is written down rather than discovered later (see `_aggregation` on blanks, and
     `_ratio_dax` on filter propagation).

Parity against the real engine is the milestone that actually verifies this (see
backend/docs/phase9_powerbi.md); these notes are what make a failure diagnosable.
"""
from insightflow_core.compilation.field_resolver import FieldResolver
from insightflow_core.compilation.measure_binding import bind_metric
from insightflow_core.models.registry import AggregationType, Measure, MetricDefinition, MetricKind
from insightflow_core.registry import MetricRegistry

# MetricFormat -> a Power BI format string. Deliberately symbol-less for money, for the same reason
# MetricFormat itself is (models/registry.py): nothing in an upload says which currency it is, so a
# renderer must not invent one.
FORMAT_STRINGS = {
    "money": "#,0.00",
    "count": "#,0",
    "percent": "0.00%",
    "number": "#,0.00",
}


def quote_table(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def column_ref(table: str, column: str) -> str:
    return f"{quote_table(table)}[{column}]"


def measure_ref(name: str) -> str:
    return f"[{name}]"


def _aggregation(measure: Measure, resolver: FieldResolver) -> str:
    """One bound Measure -> a DAX scalar expression, mirroring SQLCompiler._render_aggregation.

    Two deliberate departures from the obvious DAX, both to match the engine rather than DAX habit:
      - SUM is wrapped in COALESCE, because SQLCompiler COALESCEs SUM to 0 (an empty period is a
        routine query, and 0 is the correct identity for a sum of nothing). Bare DAX SUM returns
        BLANK there, which formats as an empty cell rather than 0.
      - COUNT_DISTINCT uses DISTINCTCOUNTNOBLANK, not DISTINCTCOUNT: SQL's COUNT(DISTINCT c) ignores
        NULLs, while DAX's DISTINCTCOUNT counts BLANK as a distinct value. On any column with an
        empty cell the two differ by exactly one -- small, silent, and wrong.
    """
    loc = resolver.resolve_field(measure.entity, measure.source_field)
    col = column_ref(loc.entity, loc.source_column)
    if measure.aggregation == AggregationType.SUM:
        return f"COALESCE(SUM({col}), 0)"
    if measure.aggregation == AggregationType.COUNT_DISTINCT:
        return f"DISTINCTCOUNTNOBLANK({col})"
    if measure.aggregation == AggregationType.COUNT:
        return f"COUNT({col})"
    if measure.aggregation == AggregationType.AVG:
        return f"AVERAGE({col})"
    return f"{measure.aggregation.value.upper()}({col})"


def _ratio_dax(numerator: Measure, denominator: Measure) -> str:
    """RATIO -> DIVIDE of the two measures, mirroring SQLCompiler._compile_ratio.

    DIVIDE returns BLANK on a zero or blank denominator, which is what the engine's
    `NULLIF(den, 0)` produces too -- AOV over a period with zero orders is genuinely undefined, not 0.

    Known, intended divergence: the engine computes a cross-entity ratio as two independent
    subqueries, so each side depends only on its own table's rows (that fix exists because a JOIN
    silently dropped a real Olist order with no payment row from BOTH sides at once). DAX instead
    propagates the report's filter context through the relationships. With no filters applied the two
    agree, which is what the parity run checks; under a slicer, Power BI's answer is the more useful
    one and is not the engine's answer. That is a property of putting a model in Power BI at all, not
    something this generator can paper over.
    """
    return f"DIVIDE({measure_ref(numerator.name)}, {measure_ref(denominator.name)})"


def _growth_dax(base_measure_name: str, date_table: str) -> str:
    """GROWTH -> current date-filter window vs. the immediately preceding equal-length window.

    The engine's GROWTH takes its two periods from the AnalyticalQuery, so there is no fixed formula
    to copy; a Power BI measure has to pick a convention. It picks the platform's own: POC 4's
    `TimeExpressionResolver._preceding_equal_length_period` -- the window ending the day before the
    current one starts, of the same length in days. So "revenue growth" means the same thing in
    Desktop as it does in chat, for any window the user selects.

    The dates come from the generated calendar, whose range is the dataset's own (see BIDateTable).
    Nothing here calls TODAY().
    """
    base = measure_ref(base_measure_name)
    dates = column_ref(date_table, "Date")
    lines = [
        f"VAR CurrentStart = MIN({dates})",
        f"VAR CurrentEnd = MAX({dates})",
        "VAR WindowDays = DATEDIFF(CurrentStart, CurrentEnd, DAY) + 1",
        f"VAR CurrentValue = {base}",
        "VAR PriorValue =",
        "    CALCULATE(",
        f"        {base},",
        f"        DATESBETWEEN({dates}, CurrentStart - WindowDays, CurrentStart - 1)",
        "    )",
        "RETURN DIVIDE(CurrentValue - PriorValue, PriorValue)",
    ]
    return "\n".join(lines)


_DAX_OPERATORS = {">=": ">=", "<=": "<=", ">": ">", "<": "<", "=": "=", "!=": "<>"}


def _having_ratio_dax(
    metric: MetricDefinition, threshold: Measure, denominator: Measure, resolver: FieldResolver
) -> str:
    """HAVING_RATIO -> share of groups clearing the threshold, mirroring SQLCompiler._compile_having_ratio.

    The engine builds a `grp` CTE (group key + per-group aggregate) and divides the count of groups
    passing `having` by the number of groups. SUMMARIZE over the *measure's* table, grouped by the
    group column, is the same thing: summarizing a fact table by a related dimension column performs
    the join and yields only groups that actually have rows -- exactly the engine's inner-join
    denominator (`COUNT(*) FROM grp`) when the grouping column lives on another table.

    `group_by_source_column` is honoured the same way the engine honours it, bypassing the
    highest-confidence tie-break: real Olist's `customer_id` canonical name covers both the per-order
    surrogate and the real per-person `customer_unique_id`, and grouping by the wrong one makes every
    group size 1 and the whole rate a structural 0.
    """
    group_entity = metric.group_by_entity or threshold.entity
    if metric.group_by_source_column:
        group_loc = resolver.resolve_field_by_source_column(
            group_entity, metric.group_by_field, metric.group_by_source_column
        )
    else:
        group_loc = resolver.resolve_field(group_entity, metric.group_by_field)

    group_col = column_ref(group_loc.entity, group_loc.source_column)
    operator = _DAX_OPERATORS[metric.having.operator]

    if group_loc.entity == threshold.entity:
        # Same-entity grouping: the engine divides by the denominator measure itself, not by the
        # group count, so this does too.
        denominator_dax = measure_ref(denominator.name)
    else:
        denominator_dax = "COUNTROWS(Grouped)"

    lines = [
        "VAR Grouped =",
        "    ADDCOLUMNS(",
        f"        SUMMARIZE({quote_table(threshold.entity)}, {group_col}),",
        f'        "@GroupValue", CALCULATE({measure_ref(threshold.name)})',
        "    )",
        f"VAR Passing = COUNTROWS(FILTER(Grouped, [@GroupValue] {operator} {_number(metric.having.value)}))",
        f"RETURN DIVIDE(Passing, {denominator_dax})",
    ]
    return "\n".join(lines)


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def measure_dax(
    resolved: Measure | MetricDefinition,
    registry: MetricRegistry,
    resolver: FieldResolver,
    date_table: str | None,
) -> tuple[str, str]:
    """-> (home_table, dax). Raises UnresolvableMeasure exactly where the engine would, and
    ValueError for a metric this export can't represent (a GROWTH metric with no date table)."""
    having = getattr(resolved, "having", None)
    bound = bind_metric(
        resolved, registry, resolver.semantic_model, having_field=having.field if having else None
    ).measures

    if isinstance(resolved, Measure) or resolved.kind == MetricKind.BASE:
        base = bound["base"]
        return base.entity, _aggregation(base, resolver)

    if resolved.kind == MetricKind.RATIO:
        numerator, denominator = bound["numerator"], bound["denominator"]
        return numerator.entity, _ratio_dax(numerator, denominator)

    if resolved.kind == MetricKind.GROWTH:
        if date_table is None:
            raise ValueError(
                "growth needs a date table, and this dataset has no usable transaction_date column to build one from"
            )
        return bound["base"].entity, _growth_dax(resolved.base_measure, date_table)

    if resolved.kind == MetricKind.HAVING_RATIO:
        threshold, denominator = bound["threshold"], bound["denominator"]
        return threshold.entity, _having_ratio_dax(resolved, threshold, denominator, resolver)

    raise ValueError(f"unhandled MetricKind: {resolved.kind}")
