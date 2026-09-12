"""Dashboard-level validation result shapes -- same pattern as POC 2's models/validation.py
(ValidationError/ValidationResult), one level up: these describe a whole DashboardSpec, not a
single AnalyticalQuery, and carry an optional `component_id` so a caller can tell which component
a given error belongs to.
"""
from typing import Optional

from pydantic import BaseModel


class DashboardValidationError(BaseModel):
    code: str
    message: str
    component_id: Optional[str] = None


class DashboardValidationResult(BaseModel):
    is_valid: bool
    errors: list[DashboardValidationError] = []
