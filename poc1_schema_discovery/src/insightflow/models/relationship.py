from typing import Optional

from pydantic import BaseModel, Field


class RelationshipCandidate(BaseModel):
    """Output of statistical candidate generation, later enriched by LLM reasoning.
    `from_uniqueness`/`to_uniqueness` (per-column, not just their max) are what
    validate() uses to deterministically pick the FK->PK direction — never the LLM's
    free-text guess, which is fragile and was previously discarded anyway."""

    from_column: str
    from_file: str
    to_column: str
    to_file: str
    name_similarity: float
    type_compatible: bool
    value_overlap_ratio: float
    from_uniqueness: float
    to_uniqueness: float
    llm_confidence: Optional[float] = None


class Relationship(BaseModel):
    """A validated relationship — only these are allowed into the semantic model."""

    from_field: str
    to_field: str
    confidence: float = Field(ge=0.0, le=1.0)
    direction: str
    validated: bool = True
