from datetime import timedelta

from insightflow_core.compilation.field_resolver import FieldResolver
from insightflow_core.compilation.measure_binding import bind_metric
from insightflow_core.models import (
    AnalyticalQuery,
    CompiledQuery,
    Measure,
    MetricDefinition,
    MetricKind,
    OperationType,
    SortSpec,
    TimeFilter,
)
from insightflow_core.models.query import HavingClause
from insightflow_core.models.registry import AggregationType
from insightflow_core.registry import MetricRegistry


class SQLCompiler:
    """AnalyticalQuery -> CompiledQuery. One `_compile_*` method per MetricKind (see
    models/registry.py), matching class_diagram.md exactly.

    Parameterization convention (class_diagram.md, "SQL parameterization convention"):
    identifiers (table/column names — already validated against the Semantic Model by
    ASTValidator before this class ever runs) are the only things safe to string-interpolate
    into `sql`. Every literal value (dates, having thresholds, limit) goes into `params`,
    never into the SQL text. SQLSafetyChecker re-verifies this independently after compile().

    Exceptions to that rule, both deliberate:
      - `having.operator` is interpolated directly, never parameterized — it's not a value, and
        HavingClause's own pydantic validator already restricts it to a 6-item allowlist
        (`{">=", "<=", "=", "!=", ">", "<"}`), so it behaves like a safe identifier, not
        attacker-controlled text.
      - `limit` is inlined as a plain `int(...)`, not bound as a parameter. It's already been
        clamped to `max_row_limit` server-side (never raw user text), and SQLSafetyChecker needs
        to read the literal number out of the SQL text itself to enforce the limit
        independently — a `$limit` placeholder wouldn't be visible to a SQL-text parser.

    Implementation note (class_diagram.md's original sketch vs. what's actually implemented,
    documented here and in docs/class_diagram.md's "Decisions made during implementation"):
    every `_compile_*`/`_apply_*` method below takes a shared `params` dict instead of stashing
    parameters on `self`, so `compile()` stays re-entrant.

    `_compile_grouped_base`'s cross-entity join-fan-out fix (docs/real_world_integration_test.md's
    "Finding 4", found via a real Groq run against real Olist data, applied to both this file and
    poc3_dashboard_generation's vendored copy) is documented in detail on that method itself.
    """

    # POC2 convention: exactly one canonical time field per entity, used for every time_filter /
    # growth-period WHERE clause. This is POC 1's own vocabulary name (see
    # poc1_schema_discovery/src/insightflow/semantic/vocabulary.py's CANONICAL_FIELDS), not an
    # invented one — confirmed against real POC 1 output, which uses "transaction_date"
    # (an earlier version of this constant used a made-up "order_date" that never actually
    # matched what POC 1 emits; that mismatch went unnoticed until tested against real POC 1
    # output, only because the hand-built sample data for this POC used the same made-up name).
    # A real per-entity date-field mapping (an entity with more than one meaningful date) is
    # still a v2 concern — not part of hld.md's settled POC2 scope — flagged here, not silently
    # hardcoded without comment.
    TIME_FIELD = "transaction_date"

    _AGG_TEMPLATES = {
        # SUM is COALESCEd to 0, the others aren't -- found during the POC 2 closure sweep, not
        # any earlier real-data test (every fixture/example so far always had at least one
        # matching row). SUM over zero matching rows is SQL NULL, which crashed the whole query
        # with an unhandled pydantic ValidationError: QueryExecutor.execute() only assigns
        # MetricResult.value when the row's "value" column isn't NULL, and MetricResult requires
        # `value`/`rows` to be exactly one of them, not neither -- a period with no revenue is a
        # completely routine query (an empty date range, a brand-new business), not an edge case,
        # so this needed fixing, not just documenting. 0 is the mathematically correct identity
        # for "sum of nothing," so COALESCE-ing it there is a real fix, not a guess -- unlike
        # AVG/MIN/MAX (no defined "empty" value, and not used by any of POC 2's ten target
        # metrics today, so left as NULL/still-crashes; see class_diagram.md's closure notes) or
        # a RATIO/HAVING_RATIO whose denominator is itself 0 after filtering (an actually
        # undefined ratio, e.g. AOV over a period with zero orders -- NULLIF already deliberately
        # produces NULL there to avoid a division-by-zero error, and that NULL is the
        # mathematically correct answer, not a bug to paper over with a fake 0 or 1).
        AggregationType.SUM: "COALESCE(SUM({col}), 0)",
        AggregationType.COUNT: "COUNT({col})",
        AggregationType.AVG: "AVG({col})",
        AggregationType.MIN: "MIN({col})",
        AggregationType.MAX: "MAX({col})",
    }

    def __init__(self, registry: MetricRegistry, field_resolver: FieldResolver, max_row_limit: int):
        self.registry = registry
        self.field_resolver = field_resolver
        self.max_row_limit = max_row_limit

    def compile(self, query: AnalyticalQuery) -> CompiledQuery:
        resolved = self.registry.resolve(query.metric)
        params: dict = {}

        # Every measure is bound to a concrete entity for THIS dataset once, up front, and the
        # pinned copies are what the _compile_* methods below receive -- so their `measure.entity`
        # reads always see a real entity name, never the registry's optional pin or None.
        having = query.having_override or getattr(resolved, "having", None)
        bound = bind_metric(
            resolved, self.registry, self.field_resolver.semantic_model, having_field=having.field if having else None
        ).measures

        if isinstance(resolved, Measure):
            sql = self._compile_base(bound["base"], query, params)
        else:
            metric = resolved
            if metric.kind == MetricKind.BASE:
                sql = self._compile_base(bound["base"], query, params)
            elif metric.kind == MetricKind.RATIO:
                sql = self._compile_ratio(bound["numerator"], bound["denominator"], query, params)
            elif metric.kind == MetricKind.GROWTH:
                sql = self._compile_growth(bound["base"], query, params)
            elif metric.kind == MetricKind.HAVING_RATIO:
                sql = self._compile_having_ratio(metric, having, bound["threshold"], bound["denominator"], params)
            else:
                raise ValueError(f"unhandled MetricKind: {metric.kind}")

        sql = self._apply_sort_limit(sql, query.sort, query.limit)
        grouped = query.operation == OperationType.GROUP_BY and query.dimension is not None
        return CompiledQuery(sql=sql, params=params, result_shape="grouped" if grouped else "scalar")

    # -- MetricKind dispatch -------------------------------------------------------------

    def _compile_base(self, measure: Measure, query: AnalyticalQuery, params: dict) -> str:
        raw_col, agg_sql = self._measure_sql(measure)

        if query.operation == OperationType.GROUP_BY and query.dimension:
            return self._compile_grouped_base(measure, query, params, raw_col)

        sql = f'SELECT {agg_sql} AS value FROM "{measure.entity}"'
        sql = self._apply_time_filter(sql, query.time_filter, measure.entity, params)
        return sql

    def _compile_grouped_base(self, measure: Measure, query: AnalyticalQuery, params: dict, raw_col: str) -> str:
        """GROUP BY path for a bare Measure / BASE-kind metric — split out of `_compile_base`
        for the join-fan-out fix below (docs/real_world_integration_test.md's "Finding 4",
        found via a real Groq run against real Olist data): grouping `revenue` (measure lives on
        `payments`, one row per order) by `price` (lives on `order_items`, one-or-more rows per
        order) used to silently multiply `SUM(payment_value)` by however many `order_items` rows
        each order had, because the plain `JOIN ... GROUP BY` this used to do produces one joined
        row per matching child row, not per row of `measure.entity`. `COUNT(DISTINCT ...)`-based
        measures were already immune (dedup happens implicitly in the aggregate itself);
        `SUM`/`AVG`/plain `COUNT` were not.

        `Relationship` carries no cardinality metadata, so there is no way to tell a safe
        (many-to-one from `measure.entity`) join from an unsafe (one-to-many from
        `measure.entity`) one at compile time — the fix below is applied unconditionally to every
        cross-entity GROUP BY instead of trying to detect which case this is.
        """
        dimension = query.dimension
        dim_entity = self.field_resolver.find_entity_for_field(dimension)
        dim_loc = self.field_resolver.resolve_field(dim_entity, dimension)
        dim_col = f'"{dim_loc.entity}"."{dim_loc.source_column}"'

        if dim_entity == measure.entity:
            # No join at all -- nothing here can fan out. Unchanged from before this fix.
            agg_sql = self._render_aggregation(measure.aggregation, raw_col)
            sql = f'SELECT {dim_col} AS "{dimension}", {agg_sql} AS value FROM "{measure.entity}"'
            sql = self._apply_time_filter(sql, query.time_filter, measure.entity, params)
            return f"{sql} GROUP BY {dim_col}"

        # Cross-entity: give every row of measure.entity a synthetic, guaranteed-unique row id
        # BEFORE joining (ROW_NUMBER() OVER (), not the join column -- the join column is only
        # unique per row of measure.entity by accident of a particular dataset's shape, e.g.
        # `payments.order_id` in the Finding-4 repro; it is not in general, e.g. `orders`'s own
        # `product_id`/`customer_id` FK columns repeat across many `orders` rows even though that
        # join direction doesn't fan out at all). De-duplicating on (row id, dimension value)
        # after the join means a row that matches several children with the SAME dimension value
        # (the actual fan-out case) is counted once, while a row matching children with
        # DIFFERENT dimension values (a genuine multi-attribution case, e.g. one order with line
        # items at two different prices) is counted once per distinct value it touches -- a
        # documented, defensible limitation, not the same failure as the original bug's blind
        # row-count duplication. This is a no-op whenever the join doesn't actually fan out (both
        # `test_category_performance_group_by` and `test_regional_performance_group_by_across_join`
        # predate this fix and must keep passing with byte-identical results).
        # CTEs, not derived-table subqueries -- SQLSafetyChecker's allowlist check (mirrored in
        # both POCs) already special-cases CTE names (see its own `_compile_having_ratio`-derived
        # comment: "our own 'grp' in HAVING_RATIO queries") but has no equivalent allowance for an
        # aliased derived table, so a bare `(SELECT ...) AS base` here would (correctly, from the
        # checker's point of view) get flagged as an unknown "table."
        left_entity, left_col, right_entity, right_col = self._resolve_join(measure.entity, dim_entity)
        base_sql = (
            f'SELECT "{left_entity}"."{left_col}" AS __join_key, {raw_col} AS __raw_value, '
            f'ROW_NUMBER() OVER () AS __row_id FROM "{measure.entity}"'
        )
        base_sql = self._apply_time_filter(base_sql, query.time_filter, measure.entity, params)
        outer_agg = self._render_aggregation(measure.aggregation, "deduped.__raw_value")
        return (
            f"WITH base AS ({base_sql}), "
            f'deduped AS ('
            f'SELECT DISTINCT base.__row_id, {dim_col} AS "{dimension}", base.__raw_value '
            f'FROM base JOIN "{right_entity}" ON base.__join_key = "{right_entity}"."{right_col}"'
            f") "
            f'SELECT deduped."{dimension}" AS "{dimension}", {outer_agg} AS value '
            f'FROM deduped GROUP BY deduped."{dimension}"'
        )

    def _compile_ratio(self, numerator: Measure, denominator: Measure, query: AnalyticalQuery, params: dict) -> str:
        _, num_agg = self._measure_sql(numerator)
        _, den_agg = self._measure_sql(denominator)

        if denominator.entity == numerator.entity:
            entity = numerator.entity
            sql = f'SELECT CAST({num_agg} AS DOUBLE) / NULLIF({den_agg}, 0) AS value FROM "{entity}"'
            sql = self._apply_time_filter(sql, query.time_filter, entity, params)
            return sql

        # Numerator and denominator live in different entities: each is its own independent
        # scalar subquery (same pattern as _compile_growth below), never a JOINed FROM clause.
        #
        # Real finding from the full-scale Olist test (docs/real_world_integration_test.md,
        # "Findings from the real-scale test"): the original implementation here JOINed
        # numerator_entity to denominator_entity and ran both aggregates over that one joined row
        # set. A JOIN only keeps rows that match on both sides, so any row on either side with no
        # counterpart on the other silently disappears from BOTH the numerator SUM and the
        # denominator COUNT at once. At real scale this stopped being theoretical: one real order
        # in the Olist data (status "delivered" -- not canceled, not synthetic) has zero matching
        # rows in `payments`, a genuine gap in the source data itself. Computed via the JOIN,
        # that order silently vanished from AOV's denominator (99,441 -> 99,440) instead of being
        # counted as a real order with $0 attributed revenue -- wrong, with no error raised. Two
        # independent subqueries make each side depend only on its own entity's rows, matching
        # what running the "orders" / "revenue" measures standalone reports; that also removes
        # the need for a join-path between the two entities at all, so a RATIO metric now works
        # even when no `Relationship` connects its numerator and denominator entities.
        num_where = self._time_filter_predicate(query.time_filter, numerator.entity, params, "num_tf") if query.time_filter else None
        den_where = self._time_filter_predicate(query.time_filter, denominator.entity, params, "den_tf") if query.time_filter else None
        num_sql = f'(SELECT {num_agg} FROM "{numerator.entity}"' + (f" WHERE {num_where})" if num_where else ")")
        den_sql = f'(SELECT {den_agg} FROM "{denominator.entity}"' + (f" WHERE {den_where})" if den_where else ")")
        return f"SELECT CAST({num_sql} AS DOUBLE) / NULLIF({den_sql}, 0) AS value"

    def _compile_growth(self, measure: Measure, query: AnalyticalQuery, params: dict) -> str:
        if query.growth is None:
            raise ValueError("GROWTH compilation requires query.growth (AnalyticalQuery already enforces this)")

        _, agg_sql = self._measure_sql(measure)
        entity = measure.entity

        cur_predicate = self._time_filter_predicate(query.growth.current_period, entity, params, "cur")
        cmp_predicate = self._time_filter_predicate(query.growth.comparison_period, entity, params, "cmp")

        cur_sql = f'(SELECT {agg_sql} FROM "{entity}" WHERE {cur_predicate})'
        cmp_sql = f'(SELECT {agg_sql} FROM "{entity}" WHERE {cmp_predicate})'

        sql = (
            f"SELECT CAST({cur_sql} - {cmp_sql} AS DOUBLE) / NULLIF({cmp_sql}, 0) AS value, "
            f"{cur_sql} AS current_value, {cmp_sql} AS comparison_value"
        )
        return sql

    def _compile_having_ratio(
        self,
        metric: MetricDefinition,
        having: HavingClause | None,
        threshold_measure: Measure,
        denominator: Measure,
        params: dict,
    ) -> str:
        if having is None:
            raise ValueError(f'metric "{metric.name}" is HAVING_RATIO but has no having clause (registry or override)')
        if not metric.group_by_field:
            raise ValueError(f'metric "{metric.name}" is HAVING_RATIO but has no group_by_field')

        entity = threshold_measure.entity
        if denominator.entity != entity:
            raise ValueError("HAVING_RATIO numerator/denominator measures must share one entity in POC2")

        group_entity = metric.group_by_entity or entity
        if metric.group_by_source_column:
            group_loc = self.field_resolver.resolve_field_by_source_column(
                group_entity, metric.group_by_field, metric.group_by_source_column
            )
        else:
            group_loc = self.field_resolver.resolve_field(group_entity, metric.group_by_field)
        group_col_sql = f'"{group_loc.entity}"."{group_loc.source_column}"'
        _, threshold_agg = self._measure_sql(threshold_measure)
        _, den_agg = self._measure_sql(denominator)

        threshold_param = "having_threshold"
        params[threshold_param] = having.value

        if group_entity == entity:
            from_clause = f'"{entity}"'
            denominator_sql = f'(SELECT {den_agg} FROM "{entity}")'
        else:
            # Cross-entity grouping (found via the full-scale real-Olist `repeat_purchase_rate`
            # finding — see MetricDefinition's group_by_entity/group_by_source_column docstring).
            # Deliberately NOT the same row-id-dedup treatment as `_compile_grouped_base`'s
            # cross-entity GROUP BY (Finding 4): that fix exists because a measure's entity can be
            # on the "one" side of the join being performed there, so joining in the dimension's
            # entity can multiply each measure row. Here it's the opposite and safe by
            # construction: `entity` (e.g. "orders") is the base of every aggregate above
            # (threshold_agg, den_agg), and `group_entity` (e.g. "customers") is joined in via a
            # single-hop FK relationship where `entity` holds the foreign key -- each row of
            # `entity` matches AT MOST ONE row of `group_entity`, so the join can only narrow rows
            # (an order with no matching customer disappears, no different from an INNER JOIN
            # anywhere else), never fan them out. `_resolve_join` doesn't tell us which side holds
            # the FK, but this method itself doesn't need to know: no row of `entity` can gain
            # extra copies from a join to a table where the join column is unique.
            left_entity, left_col, right_entity, right_col = self._resolve_join(entity, group_entity)
            from_clause = f'"{left_entity}" JOIN "{right_entity}" ON "{left_entity}"."{left_col}" = "{right_entity}"."{right_col}"'
            # `denominator_measure` was configured assuming it counts the same population being
            # grouped -- true when group_entity == entity (denominator_measure's own COUNT DISTINCT
            # of `entity`'s key matches COUNT(*) FROM grp exactly), but NOT here: `den_agg` still
            # counts `entity`'s own (surrogate) key, which is exactly the wrong-population problem
            # this fix exists to solve, not a different one. `grp` is already GROUP BY'd on the
            # real `group_col_sql`, so COUNT(*) FROM grp -- one row per distinct real group -- IS
            # the correct denominator by construction, consistent with the join now driving the
            # numerator's grouping key too.
            denominator_sql = "(SELECT COUNT(*) FROM grp)"

        sql = (
            f"WITH grp AS ("
            f"SELECT {group_col_sql} AS grp_key, {threshold_agg} AS grp_value "
            f"FROM {from_clause} GROUP BY {group_col_sql}"
            f") "
            f"SELECT CAST((SELECT COUNT(*) FROM grp WHERE grp_value {having.operator} ${threshold_param}) AS DOUBLE) "
            f"/ NULLIF({denominator_sql}, 0) AS value"
        )
        return sql

    # -- shared building blocks -----------------------------------------------------------

    def _measure_sql(self, measure: Measure) -> tuple[str, str]:
        """-> (qualified_column_sql, aggregation_sql). Table/column names come from
        FieldResolver, never built directly from the AST — by the time SQLCompiler runs,
        ASTValidator has already confirmed they exist in the Semantic Model."""
        loc = self.field_resolver.resolve_field(measure.entity, measure.source_field)
        column_sql = f'"{loc.entity}"."{loc.source_column}"'
        return column_sql, self._render_aggregation(measure.aggregation, column_sql)

    def _render_aggregation(self, agg: AggregationType, column_sql: str) -> str:
        if agg == AggregationType.COUNT_DISTINCT:
            return f"COUNT(DISTINCT {column_sql})"
        return self._AGG_TEMPLATES[agg].format(col=column_sql)

    def _resolve_join(self, from_entity: str, to_entity: str) -> tuple[str, str, str, str]:
        """Via self.field_resolver.resolve_join_path — single hop only. Returns
        `(left_entity, left_col, right_entity, right_col)` with `left_entity == from_entity`,
        i.e. the join condition is always `"{left_entity}"."{left_col}" =
        "{right_entity}"."{right_col}"` regardless of which side `Relationship.from_field`
        happened to name."""
        rel = self.field_resolver.resolve_join_path(from_entity, to_entity)
        if rel is None:
            raise ValueError(f'no single-hop relationship between "{from_entity}" and "{to_entity}"')
        left_entity, left_col = self.field_resolver._parse_field_ref(rel.from_field)
        right_entity, right_col = self.field_resolver._parse_field_ref(rel.to_field)
        if left_entity != from_entity:
            left_entity, right_entity = right_entity, left_entity
            left_col, right_col = right_col, left_col
        return left_entity, left_col, right_entity, right_col

    def _apply_time_filter(self, sql: str, time_filter: TimeFilter | None, entity: str, params: dict) -> str:
        if time_filter is None:
            return sql
        predicate = self._time_filter_predicate(time_filter, entity, params, "tf")
        where_sql = f"WHERE {predicate}"
        if " GROUP BY " in sql:
            head, _, tail = sql.partition(" GROUP BY ")
            return f"{head} {where_sql} GROUP BY {tail}"
        return f"{sql} {where_sql}"

    def _time_filter_predicate(self, time_filter: TimeFilter, entity: str, params: dict, prefix: str) -> str:
        loc = self.field_resolver.resolve_field(entity, self.TIME_FIELD)
        col = f'"{loc.entity}"."{loc.source_column}"'
        start_key, end_key = f"{prefix}_start", f"{prefix}_end"
        params[start_key] = time_filter.start_date
        # TimeFilter.end_date is a calendar `date` (models/query.py), not a timestamp -- callers
        # can only say "through this whole day," never "through this exact instant." A plain
        # `BETWEEN start AND end` binds `end` as midnight (00:00:00) of that day, so it silently
        # excludes every row timestamped later that same day.
        #
        # Real finding from the full-scale Olist test (docs/real_world_integration_test.md,
        # "Findings from the real-scale test"): the original `BETWEEN $start AND $end` predicate
        # dropped 74 real orders placed on 2017-12-31 after midnight from a `2017-01-01`..
        # `2017-12-31` growth-period filter, understating order_growth/customer_growth (2018 vs
        # 2017) as 0.1995... instead of the correct 0.1976...; the small hand-built sample never
        # had enough real timestamps landing on a range boundary to expose it. Comparing against
        # `end_date + 1 day` (exclusive) instead of `end_date` (inclusive-as-midnight) makes the
        # whole end_date day count, matching what "through end_date" means in TimeFilter's own
        # `date`-typed contract.
        params[end_key] = time_filter.end_date + timedelta(days=1)
        return f"{col} >= ${start_key} AND {col} < ${end_key}"

    def _apply_sort_limit(self, sql: str, sort: SortSpec | None, limit: int | None) -> str:
        if sort is not None:
            direction = "ASC" if sort.direction == "asc" else "DESC"
            sql = f'{sql} ORDER BY "{sort.field}" {direction}'
        effective_limit = min(limit, self.max_row_limit) if limit is not None else self.max_row_limit
        return f"{sql} LIMIT {int(effective_limit)}"
