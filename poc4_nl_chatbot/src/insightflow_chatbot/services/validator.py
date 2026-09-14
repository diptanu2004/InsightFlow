"""Pre-execution validation -- see class_diagram.md's QuestionValidator and its note on why this
wraps insightflow_core's ASTValidator directly rather than reimplementing its checks: one question
produces one or two plain AnalyticalQuery objects, and ASTValidator's own ValidationResult/
ValidationError shape is already exactly what a refusal message needs.
"""
from insightflow_core.models import ValidationError, ValidationResult
from insightflow_core.models.semantic_model import planner_dimension_problem
from insightflow_core.validation import ASTValidator

from insightflow_chatbot.models.resolved import ResolvedQuery


class QuestionValidator:
    def __init__(self, ast_validator: ASTValidator):
        self.ast_validator = ast_validator

    def validate(self, resolved: ResolvedQuery) -> ValidationResult:
        errors: list[ValidationError] = []
        for query in resolved.queries:
            errors += self.ast_validator.validate(query).errors
            # The planner is only offered categorical dimensions; refuse one it chose anyway. The
            # engine itself can group by anything, so this is a planner-output check, not ASTValidator's.
            if query.dimension is not None and (problem := planner_dimension_problem(query.dimension)) is not None:
                errors.append(ValidationError(code="dimension_not_supported", message=problem, field="dimension"))
        return ValidationResult(is_valid=not errors, errors=errors)
