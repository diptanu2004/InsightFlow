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
from typing import Literal

from pydantic import BaseModel, Field

# Whether a column-to-semantic-type mapping may be computed on. POC 1 sets "auto" or
# "needs_confirmation" at discovery from its own CONFIDENCE_THRESHOLD; a person reviewing the mapping
# sets "confirmed" or "rejected" (backend's mapping-decisions route).
FieldStatus = Literal["auto", "needs_confirmation", "confirmed", "rejected"]
TRUSTED_FIELD_STATUSES: frozenset[str] = frozenset({"auto", "confirmed"})


# Which of POC 1's canonical fields an LLM planner may group a chart or answer by. Everything else in the
# vocabulary is a date (grouping by raw timestamps gives one bar per instant -- there's no time
# bucketing yet), an amount (revenue, price: measures, not categories), or an identifier (a chart of
# random ids; real Olist has no product or customer names to fall back on). Found on the first real
# Olist dashboard, which charted revenue by transaction_date and customers by price.
#
# A closed allowlist on purpose: a field name that isn't here -- including one added to POC 1's
# vocabulary later -- is never offered, rather than silently offered. Deterministic GROUP BY through
# the analytics API is unaffected; this only constrains what LLM-planned output may choose.
PLANNER_DIMENSION_FIELDS: frozenset[str] = frozenset({"category", "region", "product_name", "customer_name"})
_DATE_FIELDS = frozenset({"transaction_date", "signup_date"})


def planner_dimension_problem(field_name: str) -> str | None:
    """None if an LLM planner may group by this field; otherwise why not, in words fit for a refusal."""
    if field_name in PLANNER_DIMENSION_FIELDS:
        return None
    if field_name in _DATE_FIELDS:
        return f'"{field_name}" is a date; grouping by individual timestamps isn\'t meaningful, and time bucketing isn\'t supported yet'
    return f'"{field_name}" is an amount or identifier, not a category to group by'


class SemanticField(BaseModel):
    name: str
    source_column: str
    source_file: str
    confidence: float
    # Defaults to "auto" only so hand-built fixtures stay terse. Real mappings always carry an explicit
    # status: POC 1 sets it on every field it emits, and datasets stored before the field existed were
    # backfilled from confidence by an Alembic data migration -- so no low-confidence mapping becomes
    # trusted just by being old.
    status: FieldStatus = "auto"


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

    def trusted(self) -> "SemanticModel":
        """The view anything that computes numbers should be built on: only mappings that are "auto" or
        "confirmed". Everything downstream -- FieldResolver, ASTValidator, measure binding, the SQL
        safety allowlist, planner contexts -- then simply never sees an unreviewed or rejected mapping,
        rather than each having to remember to skip one.

        Found in Phase 8 M4 on a real 5-file Olist upload: POC 1 mapped `freight_value -> revenue`
        (40%) and `seller_id -> customer_id` (55%), both below its own confirmation threshold. Treated
        as facts, they landed on the same entity, measure binding co-located AOV onto them, and the
        engine returned 22.82 where the true value is 160.99 -- a deterministic number built on an
        unconfirmed guess.

        An entity left with no trusted fields is dropped (QueryExecutor can't register a view for it
        anyway), and so is any relationship touching one.
        """
        entities = [
            Entity(name=e.name, fields=[f for f in e.fields if f.status in TRUSTED_FIELD_STATUSES])
            for e in self.entities
        ]
        entities = [e for e in entities if e.fields]
        kept = {e.name for e in entities}
        relationships = [
            r
            for r in self.relationships
            if r.from_field.partition(".")[0] in kept and r.to_field.partition(".")[0] in kept
        ]
        return SemanticModel(entities=entities, relationships=relationships)
