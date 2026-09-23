"""check_source_readable -- the engine's own CSV reader, run on an upload before discovery.

Found on a real Superstore export with its "People" and "Returns" sheets appended below the orders: pandas-based
discovery accepted it, and every query then failed inside DuckDB as a raw 500.
"""
import pytest

from insightflow_core.execution.query_executor import UnreadableSource, check_source_readable


def test_a_file_with_a_second_table_appended_is_rejected_with_an_actionable_reason(tmp_path):
    path = tmp_path / "orders.csv"
    # Past DuckDB's 20,480-row sniffing sample, like the real file's appended table at line 9,996 of ~10,800.
    rows = "".join(f"O{i},West,{i}\n" for i in range(25_000))
    path.write_text(f"order_id,region,sales\n{rows}Person,Region\nAnna,West\n")

    with pytest.raises(UnreadableSource) as exc:
        check_source_readable(str(path))

    message = str(exc.value)
    assert message.startswith('"orders.csv" can\'t be read as a single table: line 25002 has 2 columns where the header has 3')
    assert '("Person,Region")' in message
    assert "split it into separate CSVs" in message
    assert "sample_size" not in message  # none of DuckDB's parser-settings dump


def test_a_well_formed_file_passes(tmp_path):
    path = tmp_path / "orders.csv"
    path.write_text('order_id,order_date,name\nO1,11/8/2017,"Smith, Jo"\nO2,11/9/2017,Lee\n')
    check_source_readable(str(path))


def test_a_missing_file_is_unreadable_not_a_crash(tmp_path):
    with pytest.raises(UnreadableSource, match='"nope.csv" can\'t be read'):
        check_source_readable(str(tmp_path / "nope.csv"))


def test_a_file_duckdb_would_silently_read_as_one_column_is_rejected(tmp_path):
    """With the ragged rows inside DuckDB's sniffing sample it doesn't error: it reads each line as one text
    column. The large real file errored only because the appended table sat past row 20,480."""
    path = tmp_path / "orders.csv"
    path.write_text("order_id,region,sales\nO1,West,10\nPerson,Region\n")

    with pytest.raises(UnreadableSource, match="rows don't all have the 3 columns its header has"):
        check_source_readable(str(path))


def test_a_genuine_single_column_file_passes(tmp_path):
    path = tmp_path / "ids.csv"
    path.write_text("customer_id\nC1\nC2\n")
    check_source_readable(str(path))
