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


def test_a_windows_1252_file_is_named_as_an_encoding_problem(tmp_path):
    """Real ClassicModels `sales_data_sample.csv`: a Swedish address holds 0x84 on line 29.

    DuckDB reads UTF-8 only and says just "CSV Error on Line: 29", which reads like a ragged row.
    Two of the five datasets added for the ten-dataset survey arrived this way -- Excel on Windows
    writes Windows-1252 by default (frontend/docs/hld.md finding 11).
    """
    csv_path = tmp_path / "sales_data_sample.csv"
    rows = [b"ORDERNUMBER,CUSTOMERNAME,ADDRESSLINE1"]
    rows += [b"1010%d,Land of Toys Inc.,897 Long Airport Avenue" % (i % 10) for i in range(27)]
    rows += [b"10109,Volvo Model Replicas Co,Berguvsv\x84gen 8"]
    csv_path.write_bytes(b"\n".join(rows) + b"\n")

    with pytest.raises(UnreadableSource) as caught:
        check_source_readable(str(csv_path))
    message = str(caught.value)
    assert "isn't UTF-8 text" in message
    assert "0x84" in message
    assert "line 29" in message
    assert "CSV Error on Line" not in message


def test_the_same_file_saved_as_utf8_reads_fine(tmp_path):
    csv_path = tmp_path / "sales_data_sample.csv"
    csv_path.write_text("ORDERNUMBER,CUSTOMERNAME,ADDRESSLINE1\n10109,Volvo,Berguvsvägen 8\n", encoding="utf-8")
    check_source_readable(str(csv_path))


def test_a_multibyte_character_spanning_a_read_boundary_is_not_corruption(tmp_path):
    """The scan streams in 1 MiB chunks, so a character straddling one must not read as a bad byte.

    Placed exactly: the two bytes of "e-acute" land either side of offset 1 MiB, which is the case
    a naive per-chunk `bytes.decode` gets wrong.
    """
    from insightflow_core.execution.query_executor import _first_invalid_utf8

    boundary = 1 << 20
    e_acute = b"\xc3\xa9"
    body = b"name\n" + b"x\n" * ((boundary - 5) // 2)
    payload = body.ljust(boundary - 1, b"x") + e_acute + b"\n"
    assert payload.index(e_acute) == boundary - 1

    csv_path = tmp_path / "wide.csv"
    csv_path.write_bytes(payload)
    assert _first_invalid_utf8(str(csv_path)) is None
