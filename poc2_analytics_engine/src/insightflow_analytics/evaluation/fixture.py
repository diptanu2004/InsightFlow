import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from insightflow_core.models import AnalyticalQuery


class MetricFixture(BaseModel):
    """A hand-written AST + a known-correct expected value, per architecture doc §26's POC 2
    row — same ground-truth-benchmark pattern as POC 1's GroundTruth, just for arithmetic
    instead of schema mapping.
    """

    name: str
    query: AnalyticalQuery
    dataset_dir: str
    expected_value: Any
    tolerance: float = 1e-6

    @classmethod
    def load(cls, path: str) -> "MetricFixture":
        return cls(**json.loads(Path(path).read_text()))
