import json
from datetime import date
from pathlib import Path

import pytest

from insightflow_core.models import SemanticModel
from insightflow_core.pipeline import build_pipeline
from insightflow_core.validation import ASTValidator

from insightflow.llm.client import LLMClient
from insightflow.registry.bootstrap import bootstrap_registry
from insightflow.services.time_resolver import TimeExpressionResolver
from insightflow.services.validator import QuestionValidator

DATA_DIR = Path(__file__).parent.parent / "data"


@pytest.fixture
def semantic_model() -> SemanticModel:
    return SemanticModel(**json.loads((DATA_DIR / "semantic_model.json").read_text()))


@pytest.fixture
def registry():
    return bootstrap_registry()


@pytest.fixture
def engine(semantic_model, registry):
    """A real AnalyticsEnginePipeline (from insightflow_core, unmodified) against the real sample
    dataset, exactly as QuestionExecutor uses it in production."""
    return build_pipeline(semantic_model, registry, str(DATA_DIR / "raw" / "sample"))


@pytest.fixture
def question_validator(registry, semantic_model):
    return QuestionValidator(ASTValidator(registry, semantic_model, 1000))


@pytest.fixture
def time_resolver():
    # Sample dataset's dates are hand-picked, small, and don't matter for these fixtures --
    # reference_date/min_date just need to be plausible and reference_date >= min_date, per
    # class_diagram.md's resolved "where reference_date is sourced" question (a real pipeline
    # sources both from a MIN/MAX(order_date) signal query instead of hardcoding them like this).
    return TimeExpressionResolver(reference_date=date(2024, 8, 15), min_date=date(2023, 1, 1))


class FakeLLMClient(LLMClient):
    """Test double for the vendored LLMClient ABC -- returns a fixed structured output instead
    of calling a real Groq model, same pattern POC 1/3's own tests use."""

    def __init__(self, fixed_output):
        self.fixed_output = fixed_output
        self.last_prompt = None

    def generate_structured(self, prompt, output_schema):
        self.last_prompt = prompt
        if isinstance(self.fixed_output, Exception):
            raise self.fixed_output
        return self.fixed_output


@pytest.fixture
def make_fake_llm_client():
    return FakeLLMClient
