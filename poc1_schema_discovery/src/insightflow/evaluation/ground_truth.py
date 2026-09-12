import json
from pathlib import Path

from pydantic import BaseModel

from insightflow.models.relationship import Relationship
from insightflow.models.semantic_mapping import SemanticMapping


class GroundTruth(BaseModel):
    """Hand-labeled expected output for a sample dataset, used only by the eval harness."""

    expected_mappings: list[SemanticMapping]
    expected_relationships: list[Relationship]

    @classmethod
    def load(cls, path: str) -> "GroundTruth":
        return cls(**json.loads(Path(path).read_text()))
