import sqlglot
from sqlglot import expressions as exp

from insightflow_core.models import CompiledQuery, SemanticModel, SQLCheckResult


class SQLSafetyChecker:
    """Independent second check on the COMPILED SQL, not the AST — architecture doc §11.2's
    explicit "SQL Validation" step. See hld.md's and class_diagram.md's notes on why this is a
    separate stage from ASTValidator rather than "more validation": ASTValidator can only catch
    bad input, not a bug in SQLCompiler itself. Two independently-implemented checks agreeing is
    the actual guarantee here.

    Parsed with `sqlglot` (dialect "duckdb"), not regex/string matching — a regex-based checker
    would itself be a bypass-bug risk, defeating the point of a second layer.
    """

    DIALECT = "duckdb"

    def __init__(self, semantic_model: SemanticModel, max_row_limit: int):
        self.semantic_model = semantic_model
        self.max_row_limit = max_row_limit
        self.allowed_tables: set[str] = {e.name for e in semantic_model.entities}
        self.allowed_columns: dict[str, set[str]] = {
            e.name: {f.source_column for f in e.fields} for e in semantic_model.entities
        }

    def check(self, compiled: CompiledQuery) -> SQLCheckResult:
        violations: list[str] = []

        if not self._is_single_select(compiled.sql):
            # Nothing else is safe to inspect if this fails (e.g. it didn't even parse, or it's
            # multiple statements) — the other checks assume a single well-formed SELECT.
            return SQLCheckResult(
                is_safe=False, violations=["compiled SQL must be exactly one SELECT statement"]
            )

        if not self._tables_and_columns_are_allowlisted(compiled.sql):
            violations.append("compiled SQL references a table or column outside the semantic model")

        if not self._has_row_limit(compiled.sql):
            violations.append(f"compiled SQL must include a LIMIT clause no greater than {self.max_row_limit}")

        return SQLCheckResult(is_safe=not violations, violations=violations)

    def _is_single_select(self, sql: str) -> bool:
        try:
            statements = [s for s in sqlglot.parse(sql, read=self.DIALECT) if s is not None]
        except Exception:
            return False
        if len(statements) != 1:
            return False
        return isinstance(statements[0], exp.Select)

    def _tables_and_columns_are_allowlisted(self, sql: str) -> bool:
        try:
            parsed = sqlglot.parse_one(sql, read=self.DIALECT)
        except Exception:
            return False

        # CTE names (e.g. our own "grp" in HAVING_RATIO queries) are compiler-authored, not
        # Semantic Model entities — exclude them from the entity/table allowlist check.
        cte_names = {cte.alias_or_name for cte in parsed.find_all(exp.CTE)}

        for table in parsed.find_all(exp.Table):
            name = table.name
            if name in cte_names:
                continue
            if name not in self.allowed_tables:
                return False

        for column in parsed.find_all(exp.Column):
            table_ref = column.table
            if not table_ref or table_ref in cte_names:
                # Unqualified references are our own SELECT-list aliases or CTE-local names
                # (e.g. "value", "grp_key", "grp_value") — never attacker-controlled identifiers,
                # since SQLCompiler always table-qualifies real physical columns.
                continue
            if table_ref not in self.allowed_columns or column.name not in self.allowed_columns[table_ref]:
                return False

        return True

    def _has_row_limit(self, sql: str) -> bool:
        try:
            parsed = sqlglot.parse_one(sql, read=self.DIALECT)
        except Exception:
            return False

        limit_expr = parsed.args.get("limit")
        if limit_expr is None:
            return False

        literal = limit_expr.expression
        try:
            limit_value = int(literal.this) if literal is not None else None
        except (TypeError, ValueError):
            return False
        if limit_value is None:
            return False

        return 0 < limit_value <= self.max_row_limit
