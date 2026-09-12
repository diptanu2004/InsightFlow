from typing import Optional

from pydantic import BaseModel


class ValidationError(BaseModel):
    code: str
    message: str
    field: Optional[str] = None


class ValidationResult(BaseModel):
    is_valid: bool
    errors: list[ValidationError] = []
