from pydantic import BaseModel, Field


class SemanticMapping(BaseModel):
    """A column mapped to a canonical semantic field. `needs_confirmation` is the hook
    a future HITL layer will read — it should never be silently promoted into the
    semantic model when True."""

    source_column: str
    source_file: str
    semantic_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    needs_confirmation: bool = False
