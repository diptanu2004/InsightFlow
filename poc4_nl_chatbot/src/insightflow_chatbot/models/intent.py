"""Planner output shapes — see hld.md's "Question Intent schema" and class_diagram.md's
QuestionIntent/QuestionOperation classes.
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, model_validator

from insightflow_chatbot.models.time_expression import TimeExpression


class QuestionOperation(str, Enum):
    """A POC 4-level planning concept -- NOT insightflow_core's OperationType. GROWTH_BY_DIMENSION
    has no single-node representation in the shared AST (AnalyticalQuery's own validator makes
    `dimension` and `growth` mutually exclusive); QueryAssembler decomposes it into two ordinary
    GROUP_BY queries. See class_diagram.md's banner note for the full reasoning.
    """

    AGGREGATE = "aggregate"
    GROUP_BY = "group_by"
    GROWTH = "growth"
    GROWTH_BY_DIMENSION = "growth_by_dimension"


class QuestionIntent(BaseModel):
    """The planner's structured output for one natural-language question.

    `answerable=False` is the planner's own refusal signal (architecture doc §16) -- checked
    again, independently, by QuestionValidator once the intent is assembled into a real
    AnalyticalQuery (defense in depth, same pattern POC 2's ASTValidator / POC 3's
    DashboardValidator already establish for their own input shapes).
    """

    answerable: bool
    reason: Optional[str] = None
    metric_name: Optional[str] = None
    operation: Optional[QuestionOperation] = None
    dimension: Optional[str] = None
    time_expression: Optional[TimeExpression] = None

    @model_validator(mode="after")
    def _shape_matches_answerable(self) -> "QuestionIntent":
        if not self.answerable:
            if not self.reason:
                raise ValueError("answerable=False requires a `reason`")
            return self
        if self.metric_name is None or self.operation is None:
            raise ValueError("answerable=True requires `metric_name` and `operation`")
        needs_dimension = self.operation in (QuestionOperation.GROUP_BY, QuestionOperation.GROWTH_BY_DIMENSION)
        if needs_dimension and self.dimension is None:
            raise ValueError(f"operation={self.operation} requires `dimension`")
        if not needs_dimension and self.dimension is not None:
            raise ValueError(f"`dimension` is only valid for GROUP_BY / GROWTH_BY_DIMENSION, got operation={self.operation}")
        needs_time_expression = self.operation in (
            QuestionOperation.GROWTH,
            QuestionOperation.GROWTH_BY_DIMENSION,
        )
        if needs_time_expression and self.time_expression is None:
            raise ValueError(f"operation={self.operation} requires `time_expression`")
        return self
