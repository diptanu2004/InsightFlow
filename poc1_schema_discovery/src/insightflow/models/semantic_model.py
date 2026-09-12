from pydantic import BaseModel

from insightflow.models.relationship import Relationship


class SemanticField(BaseModel):
    name: str
    source_column: str
    source_file: str
    confidence: float


class Entity(BaseModel):
    name: str
    fields: list[SemanticField]


class SemanticModel(BaseModel):
    """The final artifact of POC 1. This is what every downstream component
    (analytics engine, dashboards, chatbot) will consume — and what a future
    FastAPI route can return as-is, since it's a plain pydantic model."""

    entities: list[Entity]
    relationships: list[Relationship]
