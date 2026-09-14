import time
from collections import Counter
from pathlib import Path

import duckdb

from insightflow_core.models import CompiledQuery, MetricResult, QueryMetadata, SemanticModel


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
            escaped_path = csv_path.replace("'", "''")
            # csv_path comes from server config (data_dir) + the semantic model, both trusted
            # inputs at pipeline-construction time — not from the AST/user query — so inlining
            # it here is a different trust boundary than SQLCompiler's identifier handling.
            self._con.execute(f'CREATE OR REPLACE VIEW "{entity.name}" AS SELECT * FROM read_csv_auto(\'{escaped_path}\')')

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
