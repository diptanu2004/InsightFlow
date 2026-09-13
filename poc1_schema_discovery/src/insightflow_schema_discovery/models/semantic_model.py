from typing import Literal

from pydantic import BaseModel

from insightflow_schema_discovery.models.relationship import Relationship


class SemanticField(BaseModel):
    name: str
    source_column: str
    source_file: str
    confidence: float
    # Carries SemanticMapper's needs_confirmation decision out of POC 1 ("auto" / "needs_confirmation")
    # instead of dropping it, so nothing downstream has to re-derive it from a copied threshold.
    # "confirmed" / "rejected" are set later by a person reviewing the mapping. Field-identical to
    # insightflow_core's SemanticField, which the backend bridges to via a JSON round-trip.
    status: Literal["auto", "needs_confirmation", "confirmed", "rejected"] = "auto"


class Entity(BaseModel):
    name: str
    fields: list[SemanticField]


class SemanticModel(BaseModel):
    """The final artifact of POC 1. This is what every downstream component
    (analytics engine, dashboards, chatbot) will consume — and what a future
    FastAPI route can return as-is, since it's a plain pydantic model."""

    entities: list[Entity]
    relationships: list[Relationship]
