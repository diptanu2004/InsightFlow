from abc import ABC, abstractmethod
from typing import Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient(ABC):
    """Provider-agnostic structured-output interface. SemanticMapper and
    RelationshipDetector depend on this, not on any concrete provider —
    swap GroqLLMClient for another implementation without touching pipeline logic."""

    @abstractmethod
    def generate_structured(self, prompt: str, output_schema: Type[T]) -> T:
        ...
