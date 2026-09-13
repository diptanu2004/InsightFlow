"""Loads QuestionFixture ground-truth benchmarks from data/*.json -- see evaluator.py and
class_diagram.md's resolved "Refusal benchmark set" question.
"""
import json
from pathlib import Path

from insightflow.models.evaluation import QuestionFixture


def load_fixtures(path: str) -> list[QuestionFixture]:
    raw = json.loads(Path(path).read_text())
    return [QuestionFixture(**item) for item in raw]
