"""Deterministic date-bounds inference -- see class_diagram.md's resolved "where reference_date
is sourced" question: computed once at pipeline construction, not per-question, not wall-clock
`date.today()`.

Reads the source CSV itself rather than through the engine's AGGREGATE path, which always casts its result
to float -- but with the engine's own CSV reader (insightflow_core's read_csv_sql), so dates parse exactly as
queries will parse them. This used to be Python's csv module plus datetime.fromisoformat, which only accepts
ISO dates: a Superstore export ("11/8/2017", which DuckDB reads fine) would have broken chat for the whole
dataset while dashboards worked.
"""
from collections import Counter
from datetime import date
from pathlib import Path
from statistics import median

import duckdb

from insightflow_core.compilation.date_expr import date_expression
from insightflow_core.execution.query_executor import read_csv_sql
from insightflow_core.models import SemanticModel

# A trailing month holding less than this share of the dataset's typical volume is treated as the
# ragged edge of an export, not as business activity. See anchor_date.
_SPARSE_TAIL_RATIO = 0.10
_MIN_HISTORY_MONTHS = 3


class NoTimeField(ValueError):
    """No entity carries the time field -- e.g. the only date column is still awaiting review."""


class UnreadableDates(ValueError):
    """The time field's column doesn't parse as dates with the engine's reader."""


class AmbiguousTimeField(ValueError):
    def __init__(self, message: str, entities: list[str]):
        super().__init__(message)
        self.entities = entities


def anchor_date(dates: list[date]) -> date:
    """The date relative periods ("last quarter", growth windows) are measured back from: the latest
    date, after trimming a sparse trailing tail.

    Found on the real Kaggle Olist data: ~6,500 orders a month through August 2018, then 16 in
    September and 4 in October -- the export simply stops. Anchored on the last stray order
    (2018-10-17), a dashboard's 90-day growth window was mostly empty months and its narrative
    reported a "-52%, deteriorating" business; "last quarter" meant a quarter with one real month.

    Rule: walking back from the last month, drop it while it holds under 10% of the median of every
    month still kept before it (only once 3 months of history exist to judge by), and anchor on the
    last real date of the month that remains. Deliberate tradeoff: a genuine one-month collapse of
    more than 90% would also be read as a truncated export -- vastly rarer than a cut-off extract,
    but not impossible. Only the anchor moves; nothing is deleted, and all-time totals still count
    every row.

    The baseline is every kept month, not the 6 immediately before the candidate, because those are
    themselves tail whenever the tail runs longer than a month or two. Found on BikeStores, whose
    orders really stop in April 2018 (125 that month) and then dribble 1, 4, 2, 1, 1, 2, 1 per month
    to December: against the six months before it, December's single order was measured against a
    median of 1.5 and cleared the bar on the first iteration, so nothing was trimmed at all. The
    anchor landed on 2018-12-28, "last quarter" meant a quarter holding 4 orders, and a dashboard
    reported a 33% decline for a business that was flat. Against the median of the whole series
    (~50) every tail month falls under the 10% bar and the anchor lands in April, as intended.
    """
    return _anchor_from_counts(Counter(dates))


def _anchor_from_counts(rows_per_day: Counter) -> date:
    by_month: Counter = Counter()
    for day, count in rows_per_day.items():
        by_month[(day.year, day.month)] += count
    months = sorted(by_month)
    kept = list(months)
    while len(kept) > _MIN_HISTORY_MONTHS:
        baseline = median([by_month[m] for m in kept[:-1]])
        if by_month[kept[-1]] >= _SPARSE_TAIL_RATIO * baseline:
            break
        kept.pop()
    last_year, last_month = kept[-1]
    return max(d for d in rows_per_day if (d.year, d.month) == (last_year, last_month))


def infer_date_bounds(
    semantic_model: SemanticModel, data_dir: str, entity_name: str | None, time_field: str
) -> tuple[date, date]:
    """`entity_name=None` resolves the entity per dataset: whichever entity carries `time_field`.

    Phase 8 M4: this used to take a fixed entity name from config (`TIME_ENTITY="orders"`), which
    only existed when a file was literally named orders.csv -- POC 1 names entities after source
    filenames, so a real upload made chat fail to construct at all. Same no-guessing rule as
    insightflow_core's measure binding: a field on more than one entity is refused rather than
    picked, and a configured `entity_name` is the explicit pin that settles it.
    """
    if entity_name is None:
        candidates = [e.name for e in semantic_model.entities if any(f.name == time_field for f in e.fields)]
        if not candidates:
            raise NoTimeField(f'no entity in this dataset has a "{time_field}" field to infer date bounds from')
        if len(candidates) > 1:
            raise AmbiguousTimeField(
                f'"{time_field}" is on more than one entity ({", ".join(candidates)}), so the date range '
                "for relative time expressions is ambiguous; set TIME_ENTITY to choose one",
                candidates,
            )
        entity_name = candidates[0]

    entity = next((e for e in semantic_model.entities if e.name == entity_name), None)
    if entity is None:
        raise ValueError(f'no entity named "{entity_name}" in the semantic model')
    matches = [f for f in entity.fields if f.name == time_field]
    if not matches:
        raise ValueError(f'entity "{entity_name}" has no field named "{time_field}"')
    # A real semantic model can map one canonical name to more than one physical column (found
    # via this real Olist dataset: "transaction_date" -> both order_purchase_timestamp,
    # confidence 0.98, and order_approved_at, confidence 0.85) -- same highest-confidence
    # tie-break FieldResolver itself uses, not just "whichever came first in the list."
    field = max(matches, key=lambda f: f.confidence)

    csv_path = Path(data_dir) / f"{field.source_file}.csv"
    column = '"' + field.source_column.replace('"', '""') + '"'
    con = duckdb.connect(":memory:")
    try:
        # Counted per day in SQL: a million-row upload shouldn't become a million Python objects.
        # The same reading the compiler's WHERE clause uses, so a period this reports as available
        # is one a query can actually select (insightflow_core.compilation.date_expr).
        day = date_expression(column)
        rows = con.execute(
            f"SELECT {day} AS day, COUNT(*) FROM ({read_csv_sql(str(csv_path))}) "
            f"WHERE {day} IS NOT NULL GROUP BY 1"
        ).fetchall()
        # date_expression yields NULL rather than raising, so "unparseable" now looks like "no rows"
        # and has to be told apart from a genuinely empty file explicitly.
        present = con.execute(
            f"SELECT COUNT(*) FROM ({read_csv_sql(str(csv_path))}) WHERE {column} IS NOT NULL"
        ).fetchone()[0]
    except duckdb.ConversionException as exc:
        raise UnreadableDates(
            f'the "{field.source_column}" column doesn\'t read as dates (it mixes formats or holds text)'
        ) from exc
    finally:
        con.close()

    rows_per_day = Counter({day: count for day, count in rows if day is not None})
    # date_expression yields NULL where the old plain CAST raised, so the strictness the old
    # behaviour got for free has to be stated: every non-null value must read as a date, or the
    # column is refused as before. Silently skipping the ones that don't would quietly drop those
    # rows from every time filter instead. A format DuckDB merely doesn't sniff (`2/24/2003 0:00`)
    # parses in full, which is the whole point of the fallback; genuine prose in a date column
    # still doesn't.
    unparsed = present - sum(rows_per_day.values())
    if unparsed > 0:
        raise UnreadableDates(
            f'the "{field.source_column}" column doesn\'t read as dates '
            f"({unparsed} of {present} values don't parse -- it mixes formats or holds text)"
        )
    if not rows_per_day:
        raise ValueError(f'no rows found in "{csv_path}" to infer date bounds from')
    return min(rows_per_day), _anchor_from_counts(rows_per_day)
