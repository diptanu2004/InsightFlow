"""What the SQL Compiler produces, and what FieldResolver resolves canonical fields down to."""
from typing import Any

from pydantic import BaseModel


class FieldLocation(BaseModel):
    entity: str
    source_file: str
    source_column: str


class CompiledQuery(BaseModel):
    """`sql` may reference identifiers (table/column names) inlined directly — those are safe
    only because ASTValidator already checked them against the Semantic Model's known names
    before SQLCompiler ran. `params` holds every literal *value* (dates, thresholds, limit) —
    these are never string-formatted into `sql`. See class_diagram.md's parameterization-
    convention note; SQLSafetyChecker re-verifies this distinction independently before
    execution.
    """

    sql: str
    params: dict[str, Any] = {}
    result_shape: str  # "scalar" | "grouped"
