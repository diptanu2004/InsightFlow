"""Vendored copy of POC 1's SemanticModel shape.

POC 1 lives in its own isolated venv (top-level README.md: "No shared virtual environment or
dependency conflicts between POCs") and its package is also named `insightflow`, so there is
still no cross-venv import path back to POC 1's real installed package -- this remains a
hand-kept mirror of poc1_schema_discovery/src/insightflow/models/semantic_model.py, not a real
dependency on it. Keep it in sync by hand if POC 1's shape changes; there's no tooling enforcing
that today.

What DID change: this used to be a separate hand-kept mirror in each of POC 2 and POC 3 (two
copies to keep in sync with POC 1, and with each other). Extracting this engine into
`insightflow-core` (see this package's own README) collapsed that down to one mirror that both
POCs now share via an editable local dependency -- POC 1 is still genuinely out of reach (a
different repo/venv this package can't depend on), but the POC-2-vs-POC-3 half of the drift risk
this docstring used to warn about is gone. If/when Phase 5 (architecture doc §29) integrates
everything into one FastAPI app, POC 1's pipeline could plausibly move into this same package too
and this mirror would collapse into a real import; until then this is the honest remaining cost.
"""
from pydantic import BaseModel, Field


class SemanticField(BaseModel):
    name: str
    source_column: str
    source_file: str
    confidence: float


class Entity(BaseModel):
    name: str
    fields: list[SemanticField]


class Relationship(BaseModel):
    from_field: str
    to_field: str
    confidence: float = Field(ge=0.0, le=1.0)
    direction: str
    validated: bool = True


class SemanticModel(BaseModel):
    entities: list[Entity]
    relationships: list[Relationship]
