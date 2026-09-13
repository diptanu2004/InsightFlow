from pydantic import BaseModel, Field


class SemanticMapping(BaseModel):
    """A column mapped to a canonical semantic field. `needs_confirmation` is the hook
    a future HITL layer will read — it should never be silently promoted into the
    semantic model when True.

    Until Phase 8 M4 it was: SchemaDiscoveryPipeline._group_into_entities copied every mapping into a
    SemanticField and dropped this flag, so unconfirmed guesses reached the analytics engine as facts
    (on a real Olist upload, `freight_value -> revenue` at 40% produced an AOV of 22.82 against a true
    160.99). It now travels as `SemanticField.status`, and the engine computes only on "auto" or
    human-"confirmed" mappings."""

    source_column: str
    source_file: str
    semantic_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    needs_confirmation: bool = False
