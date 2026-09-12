from pydantic import BaseModel


class SQLCheckResult(BaseModel):
    is_safe: bool
    violations: list[str] = []
