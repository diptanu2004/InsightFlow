"""Ground-truth benchmark shapes -- see class_diagram.md's Evaluation classes and its resolved
"Refusal benchmark set" question: fixtures cover both the answerable case (POC 1/2/3's usual
ground-truth pattern) and the unanswerable case, built from day one rather than deferred.
"""
from typing import Optional

from pydantic import BaseModel

from insightflow.models.intent import QuestionOperation


class QuestionFixture(BaseModel):
    question: str
    expected_answerable: bool
    expected_metric: Optional[str] = None
    expected_operation: Optional[QuestionOperation] = None
    expected_value: Optional[float] = None


class AnswerEvaluationReport(BaseModel):
    total: int
    intent_correct: int
    plan_correct: int
    numerically_correct: int
    refusal_correct: int
    hallucinations: int

    def summary(self) -> str:
        return (
            f"{self.total} questions -- "
            f"intent {self.intent_correct}/{self.total}, "
            f"plan {self.plan_correct}/{self.total}, "
            f"numeric {self.numerically_correct}/{self.total}, "
            f"refusal {self.refusal_correct}/{self.total}, "
            f"hallucinations {self.hallucinations}"
        )
