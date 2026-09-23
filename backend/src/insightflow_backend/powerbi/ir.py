"""The Power BI model as plain data, one step before any file format.

Phase 9's whole design rests on this split: everything that needs a decision -- which columns a
dataset may expose, which entity a measure binds to, which way a relationship points, what a metric
means in DAX -- is settled here, against types this repo already owns, and tested without Power BI
being involved at all. `tmdl.py` is then a dumb serializer over a structure that is already correct.

Nothing here is Power-BI-specific beyond vocabulary; `dataType`/`cardinality` use Power BI's own
spelling so the serializer never has to translate.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

# Power BI's own column data types, as TMDL spells them. Mapped from what DuckDB reports for the
# physical column -- read with the engine's own reader, so a column is typed here exactly as it is
# when a query aggregates it (CLAUDE.md: "anything that reads an uploaded CSV must read it the way
# the engine does").
DataType = Literal["string", "int64", "double", "dateTime", "boolean"]

# Power BI requires a cardinality on every relationship; POC 1's `Relationship` records none (it has
# from_field/to_field/confidence/direction/validated and nothing about grain). It is inferred from
# the data instead of guessed -- see build.py's `_infer_cardinality`.
Cardinality = Literal["manyToOne", "oneToMany", "oneToOne", "manyToMany"]


@dataclass(frozen=True)
class BIColumn:
    name: str          # the physical source column, used verbatim -- see BITable.columns
    data_type: DataType
    # What POC 1 thinks this column means, or None for a column carried only because a relationship
    # joins on it. Shown as the column's description in Power BI so a person reading the model in
    # Desktop sees the same mapping the review UI shows.
    canonical_field: str | None = None
    confidence: float | None = None
    is_key: bool = False


@dataclass(frozen=True)
class BITable:
    name: str
    source_file: str   # basename without extension, the way SemanticField.source_file records it
    # Sniffed with DuckDB, the engine's own reader, rather than assumed to be a comma: Power Query
    # needs it stated explicitly, and a semicolon-delimited export read as one column produces a
    # table with no usable columns rather than an error.
    delimiter: str
    # Physical column names, never canonical ones. A real semantic model maps several physical
    # columns onto one canonical name within a single entity (real Olist: customer_city,
    # customer_state and customer_zip_code_prefix all map to "region"; order_purchase_timestamp and
    # order_approved_at both to "transaction_date"), so renaming columns to canonical names would
    # produce duplicate column names and an invalid model. Keeping physical names also matches the
    # views QueryExecutor.register_sources creates, which rename nothing either -- so a column means
    # the same thing in Desktop as it does in the engine.
    columns: tuple[BIColumn, ...]


@dataclass(frozen=True)
class BIRelationship:
    from_table: str
    from_column: str   # the many side
    to_table: str
    to_column: str     # the one side
    cardinality: Cardinality
    is_active: bool = True
    # Why an edge was deactivated, for the export README. Power BI allows only one active path
    # between two tables; POC 1's relationships can form cycles (real Olist: order_items -> orders ->
    # payments alongside order_items -> payments directly).
    inactive_reason: str = ""


@dataclass(frozen=True)
class BIMeasure:
    name: str
    # Every DAX measure lives on a table. The entity its measures bind to, so a reader opening the
    # model finds `revenue` on the table it actually aggregates.
    home_table: str
    dax: str
    format_string: str
    description: str = ""


@dataclass(frozen=True)
class BIDateTable:
    """A generated calendar, needed because Power BI's time intelligence has no date table to lean on
    otherwise -- and because without one, every DAX growth measure would fall back on wall-clock
    dates. Its range comes from the dataset's own rows via `infer_date_bounds`, never `TODAY()`:
    CLAUDE.md, "never measure dates from the wall clock". `end_date` is the anchor, so the sparse
    trailing tail `anchor_date` trims (real Olist: 20 stray orders after August 2018) is outside the
    calendar rather than dragging every relative period into empty months.
    """

    table_name: str
    start_date: date
    end_date: date
    related_table: str    # the entity carrying the date column
    related_column: str   # its physical column


@dataclass(frozen=True)
class BIModel:
    name: str
    tables: tuple[BITable, ...]
    relationships: tuple[BIRelationship, ...]
    measures: tuple[BIMeasure, ...]
    date_table: BIDateTable | None = None
    # Metrics deliberately left out, with the reason -- surfaced in the export README rather than
    # silently dropped, the same way the engine refuses an unresolvable measure by name.
    omitted_metrics: tuple[tuple[str, str], ...] = field(default_factory=tuple)
