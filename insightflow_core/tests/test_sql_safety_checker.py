"""Unit tests for SQLSafetyChecker in isolation from the rest of the pipeline — proving the
independent second layer actually catches the classes of bug/attack it exists for (architecture
doc §11.2), not just that it agrees with whatever SQLCompiler happens to produce.
"""
import json
from pathlib import Path

from insightflow_core.models import CompiledQuery, SemanticModel
from insightflow_core.safety import SQLSafetyChecker

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _checker(max_row_limit: int = 1000) -> SQLSafetyChecker:
    semantic_model = SemanticModel(**json.loads((DATA_DIR / "semantic_model.json").read_text()))
    return SQLSafetyChecker(semantic_model, max_row_limit)


def test_rejects_multiple_statements():
    result = _checker().check(CompiledQuery(sql="SELECT 1; DROP TABLE orders; --", result_shape="scalar"))
    assert not result.is_safe


def test_rejects_unknown_table():
    sql = 'SELECT "secret_table"."x" AS value FROM "secret_table" LIMIT 10'
    result = _checker().check(CompiledQuery(sql=sql, result_shape="scalar"))
    assert not result.is_safe


def test_rejects_unknown_column():
    sql = 'SELECT "orders"."ssn" AS value FROM "orders" LIMIT 10'
    result = _checker().check(CompiledQuery(sql=sql, result_shape="scalar"))
    assert not result.is_safe


def test_rejects_missing_limit():
    sql = 'SELECT SUM("orders"."revenue") AS value FROM "orders"'
    result = _checker().check(CompiledQuery(sql=sql, result_shape="scalar"))
    assert not result.is_safe


def test_rejects_limit_over_max():
    sql = 'SELECT SUM("orders"."revenue") AS value FROM "orders" LIMIT 999999'
    result = _checker(max_row_limit=1000).check(CompiledQuery(sql=sql, result_shape="scalar"))
    assert not result.is_safe


def test_accepts_well_formed_query():
    sql = 'SELECT SUM("orders"."revenue") AS value FROM "orders" LIMIT 1000'
    result = _checker().check(CompiledQuery(sql=sql, result_shape="scalar"))
    assert result.is_safe
    assert result.violations == []


def test_accepts_own_cte_output():
    # SQLCompiler._compile_having_ratio's own shape — a "grp" CTE and its alias columns should
    # never be mistaken for unknown tables/columns.
    sql = (
        'WITH grp AS (SELECT "orders"."customer_id" AS grp_key, COUNT(DISTINCT "orders"."order_id") AS grp_value '
        'FROM "orders" GROUP BY "orders"."customer_id") '
        "SELECT CAST((SELECT COUNT(*) FROM grp WHERE grp_value >= $having_threshold) AS DOUBLE) "
        '/ NULLIF((SELECT COUNT(DISTINCT "orders"."customer_id") FROM "orders"), 0) AS value LIMIT 1000'
    )
    result = _checker().check(CompiledQuery(sql=sql, params={"having_threshold": 2}, result_shape="scalar"))
    assert result.is_safe
