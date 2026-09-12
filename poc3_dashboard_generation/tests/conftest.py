import json
from pathlib import Path

import pytest

from insightflow_core.compilation import FieldResolver, SQLCompiler
from insightflow_core.execution import QueryExecutor
from insightflow.llm.client import LLMClient
from insightflow_core.models import SemanticModel
from insightflow_core.pipeline import AnalyticsEnginePipeline
from insightflow.registry import bootstrap_registry
from insightflow_core.safety import SQLSafetyChecker
from insightflow_core.validation import ASTValidator

DATA_DIR = Path(__file__).parent.parent / "data"


@pytest.fixture
def semantic_model() -> SemanticModel:
    return SemanticModel(**json.loads((DATA_DIR / "semantic_model.json").read_text()))


@pytest.fixture
def registry():
    return bootstrap_registry()


@pytest.fixture
def field_resolver(semantic_model):
    return FieldResolver(semantic_model)


@pytest.fixture
def engine(semantic_model, registry, field_resolver):
    """A real AnalyticsEnginePipeline (vendored from POC 2, unmodified) against the real sample
    dataset, exactly as SignalGatherer/DashboardDataResolver use it in production."""
    executor = QueryExecutor(":memory:", 10, 1000)
    executor.register_sources(semantic_model, str(DATA_DIR / "raw" / "sample"))
    return AnalyticsEnginePipeline(
        registry=registry,
        validator=ASTValidator(registry, semantic_model, 1000),
        compiler=SQLCompiler(registry, field_resolver, 1000),
        checker=SQLSafetyChecker(semantic_model, 1000),
        executor=executor,
    )


class FakeLLMClient(LLMClient):
    """Test double for the vendored LLMClient ABC -- returns a fixed structured output instead
    of calling a real Groq model, same pattern POC 1's own tests use for SemanticMapper."""

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
    """Factory fixture (not a plain module import) so test modules don't need `tests` to be an
    importable package -- pytest's default rootdir-per-file import mode doesn't guarantee that."""
    return FakeLLMClient
