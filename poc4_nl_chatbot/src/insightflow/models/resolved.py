"""Post date-resolution, pre-validation shape -- see hld.md's "Question Intent schema" sketch."""
from typing import List

from pydantic import BaseModel

from insightflow_core.models import AnalyticalQuery

from insightflow.models.intent import QuestionIntent


class ResolvedQuery(BaseModel):
    """`queries` has exactly one element for AGGREGATE/GROUP_BY/GROWTH, and exactly two (current
    period, comparison period, same metric+dimension) for GROWTH_BY_DIMENSION -- see
    class_diagram.md's QueryAssembler.assemble_growth_by_dimension.
    """

    intent: QuestionIntent
    queries: List[AnalyticalQuery]
