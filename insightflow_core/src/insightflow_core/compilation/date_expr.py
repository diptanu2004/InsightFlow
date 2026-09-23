"""One way to read a source column as a DATE, shared by everything that reads dates.

DuckDB's CSV sniffer types `11/8/2017` and `1/5/2019` as DATE, but leaves `2/24/2003 0:00` --
the same US order with a time on the end -- as VARCHAR. `CAST(col AS DATE)` then raises
`invalid date field format ... expected format is (YYYY-MM-DD)`, which on ClassicModels'
`sales_data_sample.csv` disabled every time-based feature for the whole dataset: chat refused all
period questions and the dashboard lost its growth signals, while all-time totals kept working
(frontend/docs/hld.md finding 12).

The fallback formats are only ever reached when the plain cast yields NULL, so a column DuckDB
already types as DATE or TIMESTAMP is read exactly as it was before -- no number on any existing
dataset moves. They deliberately spell month-first, matching what DuckDB's own sniffer does for
the date-only forms of the same files: a day-first file is misread the same way it already would
be today, rather than in a new way.

This must stay the single definition. `infer_date_bounds` deciding a column parses while the
compiler's WHERE clause disagreed would hand back answers for periods the filter can't actually
select.
"""

# Month-first, with a time component -- exactly the gap in DuckDB's own sniffing. Anything without
# a time (or in ISO order) is already handled by the plain cast above it.
_FALLBACK_FORMATS = ("%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%y %H:%M")


def date_expression(column_sql: str) -> str:
    """SQL reading `column_sql` (an already-quoted column reference) as a DATE, or NULL if it can't be."""
    formats = ", ".join(f"'{fmt}'" for fmt in _FALLBACK_FORMATS)
    return (
        f"COALESCE(TRY_CAST({column_sql} AS DATE), "
        f"CAST(try_strptime(CAST({column_sql} AS VARCHAR), [{formats}]) AS DATE))"
    )
