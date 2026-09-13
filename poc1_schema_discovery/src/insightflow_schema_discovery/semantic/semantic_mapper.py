from typing import Optional

from pydantic import BaseModel

from insightflow_schema_discovery.config import settings
from insightflow_schema_discovery.llm.client import LLMClient
from insightflow_schema_discovery.models.column_profile import ColumnProfile
from insightflow_schema_discovery.models.semantic_mapping import SemanticMapping
from insightflow_schema_discovery.semantic.vocabulary import SemanticVocabulary


class _MappingItem(BaseModel):
    source_column: str
    semantic_type: str
    confidence: float


class _MappingBatch(BaseModel):
    mappings: list[_MappingItem]


class SemanticMapper:
    """Maps raw columns to canonical semantic fields, using column-profile metadata
    only — never raw rows beyond the small sample already in the profile."""

    def __init__(
        self,
        llm_client: LLMClient,
        vocabulary: Optional[SemanticVocabulary] = None,
        confidence_threshold: Optional[float] = None,
    ):
        self.llm_client = llm_client
        self.vocabulary = vocabulary or SemanticVocabulary()
        self.confidence_threshold = (
            confidence_threshold if confidence_threshold is not None else settings.confidence_threshold
        )

    def map_columns(self, source_file: str, profiles: list[ColumnProfile]) -> list[SemanticMapping]:
        batch = self.llm_client.generate_structured(self._build_prompt(profiles), _MappingBatch)
        return [
            SemanticMapping(
                source_column=item.source_column,
                source_file=source_file,
                semantic_type=item.semantic_type,
                confidence=item.confidence,
                needs_confirmation=item.confidence < self.confidence_threshold,
            )
            for item in batch.mappings
        ]

    def _build_prompt(self, profiles: list[ColumnProfile]) -> str:
        profile_block = "\n".join(
            f"- column: {p.column_name} | dtype: {p.dtype_raw} | null%: {p.null_pct} | "
            f"unique: {p.unique_count} | cardinality_ratio: {p.cardinality_ratio} | "
            f"samples: {p.sample_values} | id-like: {p.looks_like_id} | date-like: {p.looks_like_date} | "
            f"currency-like: {p.looks_like_currency}"
            for p in profiles
        )
        return (
            "You are a data schema mapping assistant.\n"
            "Map each source column below to the single best-fitting canonical field.\n"
            "If nothing fits well, use semantic_type='unknown' with a low confidence.\n\n"
            f"Canonical fields:\n{self.vocabulary.as_prompt_block()}\n\n"
            f"Source columns (profiled metadata only):\n{profile_block}\n\n"
            "Return a confidence between 0 and 1 for every mapping."
        )
