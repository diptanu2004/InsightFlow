import codecs
import csv
import re
import time
from collections import Counter
from pathlib import Path

import duckdb

from insightflow_core.models import CompiledQuery, MetricResult, QueryMetadata, SemanticModel


class UnreadableSource(ValueError):
    """A source file the engine can't read as one table."""


def read_csv_sql(csv_path: str) -> str:
    """The one way the engine reads a source CSV -- shared by register_sources' views and check_source_readable,
    so a file that passes the check is exactly a file the engine can query."""
    escaped_path = csv_path.replace("'", "''")
    return f"SELECT * FROM read_csv_auto('{escaped_path}')"


def check_source_readable(csv_path: str) -> None:
    """Read the whole file the way queries will, and raise UnreadableSource with a plain reason if that fails.

    Found on a real upload: a Superstore export with its "People" and "Returns" sheets appended below the
    orders table. Schema discovery reads files with pandas, which pads short rows with blanks, so discovery
    succeeded -- and every query then failed in DuckDB with a raw 500. Checking with the engine's own reader
    before discovery turns that into an upload error the uploader can act on.
    """
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"SELECT COUNT(*) FROM ({read_csv_sql(csv_path)})").fetchone()
        column_count = len(con.execute(f"DESCRIBE {read_csv_sql(csv_path)}").fetchall())
    except duckdb.Error as exc:
        # Encoding first: DuckDB reads UTF-8 only, and its complaint about a cp1252 byte is a bare
        # "CSV Error on Line: 29", which reads like a ragged row and tells the uploader nothing.
        # Two of the five datasets added for the ten-dataset survey arrived this way -- Excel on
        # Windows writes Windows-1252 by default (frontend/docs/hld.md finding 11).
        invalid = _first_invalid_utf8(csv_path)
        if invalid is not None:
            line, byte = invalid
            raise UnreadableSource(
                f'"{Path(csv_path).name}" isn\'t UTF-8 text: the byte 0x{byte:02x} on line {line} is not valid '
                "UTF-8. Spreadsheets often save as Windows-1252 instead; re-save the file as UTF-8 "
                "(in Excel, \"CSV UTF-8\") and upload it again."
            ) from exc
        raise UnreadableSource(describe_read_error(Path(csv_path).name, exc)) from exc
    finally:
        con.close()

    # The silent version of the same problem: when ragged rows fall inside DuckDB's sniffing sample, it doesn't
    # error -- it decides the file has no delimiter and reads every line as one text column (found writing the
    # test for the appended-table case). A header that plainly has several fields can't be a one-column table.
    header_fields = _header_field_count(csv_path)
    if column_count == 1 and header_fields > 1:
        raise UnreadableSource(
            f'"{Path(csv_path).name}" can\'t be read as a single table: its rows don\'t all have the {header_fields} '
            "columns its header has. If the file holds more than one table or a summary block, split it into "
            "separate CSVs before uploading."
        )


def _first_invalid_utf8(csv_path: str) -> tuple[int, int] | None:
    """(line number, byte value) of the first byte that isn't valid UTF-8, or None if the file is.

    Streamed through an incremental decoder rather than read whole: the largest file in the test
    set is 94 MB, and a character split across a chunk boundary must not read as corruption.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    line = 1
    try:
        handle = open(csv_path, "rb")
    except OSError:
        # Unopenable is not an encoding problem; let the caller report DuckDB's own reason.
        return None
    with handle:
        while True:
            chunk = handle.read(1 << 20)
            try:
                text = decoder.decode(chunk, final=not chunk)
            except UnicodeDecodeError as exc:
                # exc.object is the decoder's pending bytes plus this chunk; the carried bytes are
                # continuation bytes of one character, so they hold no newlines of their own.
                return line + exc.object[: exc.start].count(b"\n"), exc.object[exc.start]
            line += text.count("\n")
            if not chunk:
                return None


def _header_field_count(csv_path: str) -> int:
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        header = next((line for line in f if line.strip()), "")
    delimiter = max(",;\t|", key=header.count)
    return len(next(csv.reader([header], delimiter=delimiter), [])) if header.count(delimiter) else 1


def describe_read_error(file_name: str, exc: Exception) -> str:
    """DuckDB's CSV errors run to twenty lines of parser settings; keep what a person can act on."""
    text = str(exc)
    line = re.search(r"CSV Error on Line: (\d+)", text)
    columns = re.search(r"Expected Number of Columns: (\d+) Found: (\d+)", text)
    original = re.search(r"Original Line:\s*\n(.*)", text)
    if line and columns:
        snippet = f' ("{original.group(1).strip()[:80]}")' if original and original.group(1).strip() else ""
        return (
            f'"{file_name}" can\'t be read as a single table: line {line.group(1)} has {columns.group(2)} columns '
            f"where the header has {columns.group(1)}{snippet}. If the file holds more than one table or a summary "
            "block, split it into separate CSVs before uploading."
        )
    first_line = text.strip().splitlines()[0] if text.strip() else type(exc).__name__
    return f'"{file_name}" can\'t be read: {first_line}'


class QueryExecutor:
    """Runs a CHECKED CompiledQuery against DuckDB, in-process, no server (hld.md: "DuckDB,
    in-process, no server"). Enforces row-cap again at execution time as a defensive backstop —
    ASTValidator is authoritative for rejection; this is the "should never fire" safety net
    (hld.md's "Row-limit / timeout enforcement" decision).

    Deliberately returns plain `list[dict]` rows rather than a pandas DataFrame — DuckDB's own
    `.fetchdf()` requires pandas, which isn't a POC2 dependency (see pyproject.toml); raw
    `fetchall()` + `.description` gives the same shape without adding one.
    """

    def __init__(self, duckdb_path: str, timeout_seconds: int, row_cap: int):
        self.duckdb_path = duckdb_path
        self.timeout_seconds = timeout_seconds
        self.row_cap = row_cap
        self._con = duckdb.connect(duckdb_path)

    def register_sources(self, semantic_model: SemanticModel, data_dir: str) -> None:
        """One DuckDB view per entity, over that entity's own source CSV, columns left exactly
        as they appear in the file (i.e. under their `source_column` names) — SQLCompiler
        resolves canonical field names down to these physical column names via FieldResolver
        before any SQL is built, so the views don't need to rename anything.

        `source_file` -> `.csv`, found via a real cross-POC integration test (running this
        against POC 1's actual output, not just the sample semantic_model.json hand-built for
        this repo): POC 1's real `SemanticField.source_file` is the bare basename with NO
        extension (`"orders"`, not `"orders.csv"`) — confirmed against two independent real
        POC 1 runs (the Olist eval output and the earlier sample-dataset output), not just this
        one dataset. The hand-built sample data in this repo happened to use `"orders.csv"`
        directly as `source_file`, which is why this bug didn't surface until tested against
        real POC 1 output. Real gap this leaves open: POC 1's SemanticModel doesn't record the
        *original* file format anywhere, so POC 2 can't actually tell a CSV-sourced entity from
        an Excel-sourced one (POC 1's own FileParser accepts `.xlsx` too, per its pyproject.toml)
        — POC2 just assumes `.csv` for every entity, which is fine for both datasets tested so
        far but would break silently against an Excel-sourced semantic model.

        Simplifying assumption: if an entity's fields span more than one `source_file` (POC 1
        *can* produce that), this registers only the most common one. Fine for the sample and
        Olist test data; a real multi-file entity would need a proper multi-source view, which
        is out of scope here (same "single-hop, single-source" simplicity as FieldResolver).
        """
        for entity in semantic_model.entities:
            if not entity.fields:
                continue
            source_file = Counter(f.source_file for f in entity.fields).most_common(1)[0][0]
            csv_path = str(Path(data_dir) / f"{source_file}.csv")
            # csv_path comes from server config (data_dir) + the semantic model, both trusted
            # inputs at pipeline-construction time — not from the AST/user query — so inlining
            # it here is a different trust boundary than SQLCompiler's identifier handling.
            self._con.execute(f'CREATE OR REPLACE VIEW "{entity.name}" AS {read_csv_sql(csv_path)}')

    def execute(self, compiled: CompiledQuery, metric_name: str) -> MetricResult:
        start = time.perf_counter()
        rows = self._run_with_timeout(compiled.sql, compiled.params)
        rows = self._enforce_row_cap(rows)
        elapsed_ms = (time.perf_counter() - start) * 1000

        metadata = QueryMetadata(sql=compiled.sql, execution_time_ms=elapsed_ms, row_count=len(rows))

        if compiled.result_shape == "scalar":
            value = None
            if rows and rows[0].get("value") is not None:
                value = float(rows[0]["value"])
            caveats = [
                rule.message
                for rule in compiled.caveat_rules
                if rows and rows[0].get(rule.column) is not None and float(rows[0][rule.column]) <= rule.at_most
            ]
            return MetricResult(metric_name=metric_name, shape="scalar", value=value, metadata=metadata, caveats=caveats)

        return MetricResult(
            metric_name=metric_name,
            shape="grouped",
            rows=rows,
            metadata=metadata,
            # Filling the limit exactly may also mean "exactly that many groups exist" -- callers treat
            # it as "may be incomplete", which is the only safe reading.
            truncated=compiled.row_limit > 0 and len(rows) >= compiled.row_limit,
        )

    def fetch_rows(self, compiled: CompiledQuery) -> list[dict]:
        """Raw rows for a checked lookup query (e.g. SQLCompiler.compile_value_lookup) that isn't a metric."""
        return self._enforce_row_cap(self._run_with_timeout(compiled.sql, compiled.params))

    def _run_with_timeout(self, sql: str, params: dict) -> list[dict]:
        # DuckDB's Python API has no first-class per-query wall-clock timeout that's portable
        # across versions; ASTValidator's row-limit check is authoritative (hld.md), and
        # `_enforce_row_cap` below is the backstop on the way out. A hard timeout here is future
        # work if a query proves slow in practice — flagged rather than silently unimplemented.
        cursor = self._con.execute(sql, params) if params else self._con.execute(sql)
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _enforce_row_cap(self, rows: list[dict]) -> list[dict]:
        return rows[: self.row_cap] if len(rows) > self.row_cap else rows
