"""SemanticModel + MetricRegistry + the dataset's own CSVs -> a BIModel.

Everything Power BI needs that POC 1 doesn't record is worked out here from the data itself rather
than assumed: column data types, relationship cardinality, which relationships have to be
deactivated, and the calendar's range. That keeps Phase 9 inside the project's own rule -- the LLM
understands, deterministic code calculates -- with no LLM involved in the export at all.
"""
from collections import Counter
from pathlib import Path

import duckdb

from insightflow_core.compilation.field_resolver import FieldResolver
from insightflow_core.compilation.measure_binding import UnresolvableMeasure
from insightflow_core.execution.query_executor import read_csv_sql
from insightflow_core.models.semantic_model import SemanticModel
from insightflow_core.models.registry import MetricDefinition, MetricKind
from insightflow_core.registry import MetricRegistry

from insightflow_backend.config import settings
from insightflow_backend.powerbi import dax as dax_mod
from insightflow_backend.powerbi.ir import (
    BIColumn,
    BIDateTable,
    BIMeasure,
    BIModel,
    BIRelationship,
    BITable,
    Cardinality,
    DataType,
)

DATE_TABLE_NAME = "Calendar"

# The canonical date field, same constant SQLCompiler.TIME_FIELD uses. Imported rather than
# redeclared would be better, but it is a class attribute on the compiler; kept in sync by the test
# that asserts they are equal.
TIME_FIELD = "transaction_date"

_DUCKDB_TYPES: dict[str, DataType] = {
    "BOOLEAN": "boolean",
    "TINYINT": "int64",
    "SMALLINT": "int64",
    "INTEGER": "int64",
    "BIGINT": "int64",
    "HUGEINT": "int64",
    "UTINYINT": "int64",
    "USMALLINT": "int64",
    "UINTEGER": "int64",
    "UBIGINT": "int64",
    "FLOAT": "double",
    "DOUBLE": "double",
    "DATE": "dateTime",
    "TIMESTAMP": "dateTime",
    "TIMESTAMP WITH TIME ZONE": "dateTime",
}


def _data_type(duckdb_type: str) -> DataType:
    base = duckdb_type.upper().split("(")[0].strip()
    if base.startswith("DECIMAL"):
        return "double"
    return _DUCKDB_TYPES.get(base, "string")


class _SourceProfile:
    """One pass over each source CSV, with the engine's own reader.

    Deliberately `read_csv_sql`, not a second CSV reader: CLAUDE.md's rule after a Superstore export
    passed pandas-based discovery and then failed every DuckDB query. A file the export can profile
    is exactly a file the engine can query.
    """

    def __init__(self, data_dir: Path):
        self._data_dir = data_dir
        self._con = duckdb.connect(":memory:")
        self._types: dict[str, dict[str, DataType]] = {}
        self._unique: dict[tuple[str, str], bool] = {}
        self._delimiters: dict[str, str] = {}

    def _relation(self, source_file: str) -> str:
        return read_csv_sql(str(self._data_dir / f"{source_file}.csv"))

    def types(self, source_file: str) -> dict[str, DataType]:
        if source_file not in self._types:
            rows = self._con.execute(f"DESCRIBE {self._relation(source_file)}").fetchall()
            self._types[source_file] = {name: _data_type(str(dtype)) for name, dtype, *_ in rows}
        return self._types[source_file]

    def delimiter(self, source_file: str) -> str:
        """DuckDB's own sniffer, so the delimiter Power Query is told matches the one the engine reads
        the file with. Falls back to a comma if sniffing yields nothing usable."""
        if source_file not in self._delimiters:
            path = str(self._data_dir / f"{source_file}.csv").replace("'", "''")
            try:
                row = self._con.execute(f"SELECT Delimiter FROM sniff_csv('{path}')").fetchone()
                self._delimiters[source_file] = (row[0] if row and row[0] else ",")[:1] or ","
            except duckdb.Error:
                self._delimiters[source_file] = ","
        return self._delimiters[source_file]

    def is_unique(self, source_file: str, column: str) -> bool:
        """Whether `column` identifies a row -- the fact Power BI calls cardinality and POC 1 never
        records. Measured, because guessing it wrong is exactly how a model silently double-counts:
        a relationship declared many-to-one the wrong way round makes Power BI fan a SUM out across
        the many side, the same failure `_compile_grouped_base`'s ROW_NUMBER dedup exists to prevent
        in SQL.
        """
        key = (source_file, column)
        if key not in self._unique:
            quoted = '"' + column.replace('"', '""') + '"'
            total, distinct = self._con.execute(
                f"SELECT COUNT({quoted}), COUNT(DISTINCT {quoted}) FROM ({self._relation(source_file)})"
            ).fetchone()
            self._unique[key] = total == distinct and total > 0
        return self._unique[key]

    def close(self) -> None:
        self._con.close()


def _source_file(entity_name: str, semantic_model: SemanticModel) -> str:
    entity = next(e for e in semantic_model.entities if e.name == entity_name)
    # Same "most common source_file per entity" rule QueryExecutor.register_sources uses, so the
    # export reads the file the engine reads.
    return Counter(f.source_file for f in entity.fields).most_common(1)[0][0]


def _join_columns(semantic_model: SemanticModel) -> set[tuple[str, str]]:
    """(entity, physical column) pairs a relationship joins on.

    Carried into the model even when no trusted field maps them, mirroring what the SQL safety
    checker already allowlists: a mapping review can reject a key column's *meaning* while the
    physical foreign key is still the thing that joins the tables. Without these, a reviewed dataset
    would export as a set of tables with no relationships at all.
    """
    resolver = FieldResolver(semantic_model)
    pairs = set()
    for rel in semantic_model.relationships:
        for ref in (rel.from_field, rel.to_field):
            pairs.add(resolver._parse_field_ref(ref))
    return pairs


def _build_tables(semantic_model: SemanticModel, profile: _SourceProfile) -> tuple[BITable, ...]:
    join_columns = _join_columns(semantic_model)
    tables = []
    for entity in semantic_model.entities:
        source_file = _source_file(entity.name, semantic_model)
        types = profile.types(source_file)
        # Best trusted mapping per physical column: a column can carry several (real Olist maps
        # customer_city, customer_state and customer_zip_code_prefix all onto "region"), and the
        # description should show the one the engine would resolve -- highest confidence.
        best: dict[str, tuple[str, float]] = {}
        for f in entity.fields:
            current = best.get(f.source_column)
            if current is None or f.confidence > current[1]:
                best[f.source_column] = (f.name, f.confidence)

        column_names = list(best)
        for entity_name, column in sorted(join_columns):
            if entity_name == entity.name and column not in best and column in types:
                column_names.append(column)

        columns = tuple(
            BIColumn(
                name=name,
                data_type=types.get(name, "string"),
                canonical_field=best[name][0] if name in best else None,
                confidence=best[name][1] if name in best else None,
                is_key=(entity.name, name) in join_columns,
            )
            for name in column_names
            if name in types
        )
        if columns:
            tables.append(
                BITable(
                    name=entity.name,
                    source_file=source_file,
                    delimiter=profile.delimiter(source_file),
                    columns=columns,
                )
            )
    return tuple(tables)


def _infer_cardinality(
    from_entity: str, from_column: str, to_entity: str, to_column: str, semantic_model: SemanticModel, profile: _SourceProfile
) -> tuple[str, str, str, str, Cardinality]:
    """-> (many_table, many_column, one_table, one_column, cardinality), measured from the data.

    Power BI's `fromColumn` is the many side by convention, so the pair is swapped if POC 1 happened
    to record the relationship the other way round -- `Relationship.direction` is a display string
    built from the same two field refs, not independent evidence of grain.
    """
    from_unique = profile.is_unique(_source_file(from_entity, semantic_model), from_column)
    to_unique = profile.is_unique(_source_file(to_entity, semantic_model), to_column)

    if from_unique and to_unique:
        return from_entity, from_column, to_entity, to_column, "oneToOne"
    if to_unique:
        return from_entity, from_column, to_entity, to_column, "manyToOne"
    if from_unique:
        return to_entity, to_column, from_entity, from_column, "manyToOne"
    return from_entity, from_column, to_entity, to_column, "manyToMany"


def _build_relationships(semantic_model: SemanticModel, profile: _SourceProfile) -> tuple[BIRelationship, ...]:
    """POC 1's relationships, oriented and de-cycled for Power BI.

    Power BI allows only one active path between any two tables, and POC 1's output routinely has
    more: real Olist detects order_items -> orders, orders -> payments AND order_items -> payments,
    a genuine cycle. Loading that leaves Desktop to deactivate an edge of its own choosing, so the
    choice is made here instead, deterministically -- highest confidence first, and any edge that
    would close a loop over already-connected tables is marked inactive with its reason. That keeps
    the strongest detected relationships active, which on Olist deactivates the direct
    order_items -> payments edge (0.99) in favour of the two-hop path through orders (0.995).
    """
    resolver = FieldResolver(semantic_model)
    known = {e.name for e in semantic_model.entities}

    parent: dict[str, str] = {name: name for name in known}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    relationships = []
    ordered = sorted(semantic_model.relationships, key=lambda r: r.confidence, reverse=True)
    for rel in ordered:
        from_entity, from_column = resolver._parse_field_ref(rel.from_field)
        to_entity, to_column = resolver._parse_field_ref(rel.to_field)
        if from_entity not in known or to_entity not in known or from_entity == to_entity:
            continue

        many_table, many_column, one_table, one_column, cardinality = _infer_cardinality(
            from_entity, from_column, to_entity, to_column, semantic_model, profile
        )

        root_a, root_b = find(from_entity), find(to_entity)
        if root_a == root_b:
            relationships.append(
                BIRelationship(
                    from_table=many_table,
                    from_column=many_column,
                    to_table=one_table,
                    to_column=one_column,
                    cardinality=cardinality,
                    is_active=False,
                    inactive_reason=(
                        f'"{from_entity}" and "{to_entity}" are already connected by a higher-confidence path; '
                        "Power BI allows only one active path between two tables"
                    ),
                )
            )
            continue

        parent[root_a] = root_b
        relationships.append(
            BIRelationship(
                from_table=many_table,
                from_column=many_column,
                to_table=one_table,
                to_column=one_column,
                cardinality=cardinality,
            )
        )
    return tuple(relationships)


def _build_date_table(semantic_model: SemanticModel, data_dir: Path) -> BIDateTable | None:
    """The calendar every growth measure is measured against, or None if this dataset has no single
    trusted date column.

    Resolved exactly the way chat resolves it (`wiring.build_chatbot` -> `infer_date_bounds` with
    `settings.time_entity`), so the export and the chatbot agree on which column is "the" date for a
    dataset, including when an explicit TIME_ENTITY settles an otherwise-ambiguous one. Its own
    refusals -- no time field, more than one candidate entity, a column that doesn't parse as dates
    -- become "no calendar, and growth metrics omitted with that reason", not a failed export.

    Range comes from `infer_date_bounds`, so it ends at the dataset's own anchor -- the sparse
    trailing tail it trims (real Olist: ~6,500 orders a month to August 2018, then 20 stray rows) is
    left outside the calendar. A calendar running to the last stray row is how POC 3's signals once
    reported a real -10% as "-52%, deteriorating".
    """
    from insightflow_chatbot.services.date_bounds import infer_date_bounds

    try:
        min_date, anchor = infer_date_bounds(
            semantic_model, str(data_dir), settings.time_entity, TIME_FIELD
        )
    except (ValueError, duckdb.Error):
        return None

    entity_name = settings.time_entity or next(
        e.name for e in semantic_model.entities if any(f.name == TIME_FIELD for f in e.fields)
    )
    entity = next(e for e in semantic_model.entities if e.name == entity_name)
    best = max((f for f in entity.fields if f.name == TIME_FIELD), key=lambda f: f.confidence)
    return BIDateTable(
        table_name=DATE_TABLE_NAME,
        start_date=min_date,
        end_date=anchor,
        related_table=entity_name,
        related_column=best.source_column,
    )


def _build_measures(
    semantic_model: SemanticModel, registry: MetricRegistry, date_table: str | None
) -> tuple[tuple[BIMeasure, ...], tuple[tuple[str, str], ...]]:
    resolver = FieldResolver(semantic_model)
    measures: list[BIMeasure] = []
    omitted: list[tuple[str, str]] = []

    # Tier 1 measures first: Tier 2 DAX refers to them by name, so they have to exist as real
    # measures in the model rather than being inlined -- which also means a person reading AOV in
    # Desktop sees `DIVIDE([revenue], [orders])`, the registry definition, not a wall of SQL-ish DAX.
    named: list[tuple[str, object, str]] = [
        (name, measure, measure.format) for name, measure in registry.measures.items()
    ]
    named += [(name, metric, metric.format) for name, metric in registry.metrics.items()]

    for name, resolved, fmt in named:
        try:
            home_table, expression = dax_mod.measure_dax(resolved, registry, resolver, date_table)
        except (UnresolvableMeasure, ValueError, KeyError) as exc:
            omitted.append((name, str(exc)))
            continue
        measures.append(
            BIMeasure(
                name=name,
                home_table=home_table,
                dax=expression,
                format_string=dax_mod.FORMAT_STRINGS.get(fmt, "#,0.00"),
                description=_describe(resolved),
            )
        )
    return tuple(measures), tuple(omitted)


def _describe(resolved: object) -> str:
    """The registry's description, plus the engine's own caveat where one can apply.

    `MetricResult.caveats` is a runtime protection and cannot travel into a .pbix: a caveated number
    in Desktop looks like any other number. So the *condition* travels instead, stated as a
    condition. This mirrors SQLCompiler._compile_having_ratio's CaveatRule exactly -- same trigger
    (an "at least N>1" threshold), same warning -- rather than inventing a new one. It is a note in a
    tooltip, not a guardrail; CLAUDE.md's "a prompt instruction is not a guardrail" applies just as
    well to a description field, which is why the export README says so too.
    """
    description = getattr(resolved, "description", "") or ""
    if (
        isinstance(resolved, MetricDefinition)
        and resolved.kind == MetricKind.HAVING_RATIO
        and resolved.having is not None
        and resolved.having.operator in (">=", ">")
        and float(resolved.having.value) > 1
    ):
        description += (
            f'\n\nCaveat: if every "{resolved.group_by_field}" in this dataset has at most one '
            f'"{resolved.having.field}", this rate is 0 by construction -- that identifies a single '
            "record rather than a stable one, and cannot measure repeats."
        )
    return description.strip()


def build_bi_model(
    name: str, semantic_model: SemanticModel, registry: MetricRegistry, data_dir: Path
) -> BIModel:
    """`semantic_model` must already be `.trusted()` -- see powerbi/export.py, which is the only
    caller and does that. Passing a raw model here would export unreviewed mappings as facts."""
    profile = _SourceProfile(data_dir)
    try:
        tables = _build_tables(semantic_model, profile)
        relationships = _build_relationships(semantic_model, profile)
    finally:
        profile.close()

    date_table = _build_date_table(semantic_model, data_dir)
    measures, omitted = _build_measures(
        semantic_model, registry, date_table.table_name if date_table else None
    )
    return BIModel(
        name=name,
        tables=tables,
        relationships=relationships,
        measures=measures,
        date_table=date_table,
        omitted_metrics=omitted,
    )
