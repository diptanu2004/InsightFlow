from typing import Optional, Type, TypeVar

from langchain_groq import ChatGroq
from pydantic import BaseModel

from insightflow_schema_discovery.config import settings
from insightflow_schema_discovery.llm.client import LLMClient

T = TypeVar("T", bound=BaseModel)


class GroqLLMClient(LLMClient):
    """LLMClient implementation backed by LangChain's ChatGroq.

    Uses `with_structured_output(..., method="json_mode")` rather than the default
    function-calling method: several Groq-hosted models will happily answer in prose
    instead of invoking the forced tool, which raises `tool_use_failed`. JSON mode is
    more reliable there, but — unlike function-calling — it does NOT constrain the
    model to exact field names on its own, so the prompt must spell out the real JSON
    Schema (property names included) rather than a loose type hint. Callers
    (SemanticMapper, RelationshipDetector) still don't need to know any of this.
    """

    def __init__(self, model: Optional[str] = None, temperature: float = 0.0):
        self._llm = ChatGroq(
            model=model or settings.groq_model,
            temperature=temperature,
            groq_api_key=settings.groq_api_key,
        )

    def generate_structured(self, prompt: str, output_schema: Type[T]) -> T:
        structured_llm = self._llm.with_structured_output(output_schema, method="json_mode")
        instruction = (
            "\n\nRespond with a single valid JSON object only \u2014 no markdown, no code "
            "fences, no explanation. You MUST use exactly these property names, unchanged "
            "and unabbreviated \u2014 do not rename, translate, or add extra fields. "
            f"JSON Schema:\n{self._schema_json(output_schema)}"
        )
        return structured_llm.invoke(prompt + instruction)

    @staticmethod
    def _schema_json(schema: Type[BaseModel]) -> str:
        import json

        return json.dumps(schema.model_json_schema())
