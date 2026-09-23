"""One reading of a date column, shared by the compiler's WHERE clause and infer_date_bounds.

frontend/docs/hld.md finding 12: DuckDB's sniffer types `11/8/2017` as DATE but leaves
`2/24/2003 0:00` as VARCHAR, so ClassicModels' `sales_data_sample.csv` lost every time-based
feature -- chat refused all period questions while all-time totals kept working.
"""
import duckdb
import pytest

from insightflow_core.compilation.date_expr import date_expression


def _read(rows: list[str], tmp_path, name="orders.csv") -> str:
    path = tmp_path / name
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return str(path).replace("\\", "/")


def _bounds(path: str, column: str):
    expression = date_expression(f'"{column}"')
    sql = (
        f"SELECT MIN({expression}), MAX({expression}), COUNT({expression}), COUNT(*) "
        f"FROM read_csv_auto('{path}')"
    )
    return duckdb.connect().execute(sql).fetchone()


def test_a_us_datetime_column_duckdb_leaves_as_text_still_reads_as_dates(tmp_path):
    path = _read(["ORDERNUMBER,ORDERDATE", "10107,2/24/2003 0:00", "10121,5/7/2003 0:00", "10425,5/31/2005 0:00"], tmp_path)
    minimum, maximum, parsed, total = _bounds(path, "ORDERDATE")
    assert (str(minimum), str(maximum)) == ("2003-02-24", "2005-05-31")
    assert parsed == total == 3


def test_a_column_duckdb_already_types_as_a_date_reads_exactly_as_the_plain_cast_did(tmp_path):
    """The invariant that matters: where the old `CAST(col AS DATE)` worked, nothing moves.

    Asserted against the plain cast rather than against a literal, because which of `11/8/2017`'s
    two readings DuckDB picks is its sniffer's decision about the whole file, not ours -- it reads
    the real Superstore export month-first and this two-row sample day-first.
    """
    path = _read(["id,Order Date", "1,11/8/2017", "2,1/5/2019"], tmp_path)
    connection = duckdb.connect()
    plain = connection.execute(
        f"SELECT MIN(CAST(\"Order Date\" AS DATE)), MAX(CAST(\"Order Date\" AS DATE)) FROM read_csv_auto('{path}')"
    ).fetchone()
    minimum, maximum, parsed, total = _bounds(path, "Order Date")
    assert (minimum, maximum) == plain
    assert parsed == total == 2


def test_an_iso_timestamp_is_unchanged(tmp_path):
    path = _read(["id,order_purchase_timestamp", "1,2016-09-04 21:15:19", "2,2018-10-17 17:30:18"], tmp_path)
    minimum, maximum, parsed, _ = _bounds(path, "order_purchase_timestamp")
    assert (str(minimum), str(maximum)) == ("2016-09-04", "2018-10-17")
    assert parsed == 2


def test_text_that_is_not_a_date_reads_as_null_rather_than_raising(tmp_path):
    """NULL, not an exception: callers decide what "no usable dates" means for them."""
    path = _read(["id,when", "1,not a date", "2,also not"], tmp_path)
    _, _, parsed, total = _bounds(path, "when")
    assert parsed == 0 and total == 2


@pytest.mark.parametrize("value,expected", [("2/24/2003 0:00", "2003-02-24"), ("12/31/2004 13:45", "2004-12-31")])
def test_month_first_matches_what_duckdb_does_for_the_date_only_form(value, expected, tmp_path):
    path = _read(["id,when", f"1,{value}"], tmp_path)
    minimum, _, _, _ = _bounds(path, "when")
    assert str(minimum) == expected
