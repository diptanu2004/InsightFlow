from insightflow_core.models import FieldLocation, Relationship, SemanticModel


class FieldResolver:
    """Canonical field -> physical location, plus single-hop join-path lookup.

    Multi-hop joins are deliberately unsupported — see hld.md's "Multi-hop joins are out of
    scope for POC 2" and class_diagram.md's note on why (fan-out/double-counting risk, and the
    fix belongs in POC 1's star-schema modeling, not here). `resolve_join_path` returning
    `Optional[Relationship]` rather than a path/list is a real constraint, not unfinished
    generality.

    POC 1's `Relationship.from_field`/`to_field` are `"entity.column"` strings (e.g.
    `"orders.customer_id"`), not structured references — `_parse_field_ref` is where that string
    gets parsed, kept in exactly one place so `SQLCompiler` never has to know about it.

    Implementation note (found while filling in this stub): `resolve_field` originally took
    just `canonical_field`, but the same canonical name can exist in more than one entity (e.g.
    both `orders` and `customers` carry a `customer_id` field) — a bare-name lookup is
    ambiguous. It now takes `entity` too, and `find_entity_for_field` was added for the one
    place that still needs to go from a bare AST field (a `dimension` or `having.field`) to the
    entity it lives in. See class_diagram.md's "Decisions made during implementation" note.

    Second implementation note (found via the real-dataset integration test against POC 1's
    actual Olist output, not the hand-built sample data): the SAME canonical name can also
    collide *within* one entity — POC 1 mapped both `customer_id` and `customer_unique_id` to
    canonical `customer_id` inside the `customers` entity, and both `customer_city` and
    `customer_state` (among others) to canonical `region`. `resolve_field` now picks the
    highest-`confidence` match rather than silently taking whichever came first in the list —
    still an imperfect tie-break (see the note below on `customer_id` vs `customer_unique_id`),
    but a deliberate, visible one instead of an accidental one.
    """

    def __init__(self, semantic_model: SemanticModel):
        self.semantic_model = semantic_model

    def resolve_field(self, entity: str, canonical_field: str) -> FieldLocation:
        for e in self.semantic_model.entities:
            if e.name != entity:
                continue
            matches = [f for f in e.fields if f.name == canonical_field]
            if not matches:
                raise KeyError(f'entity "{entity}" has no field named "{canonical_field}"')
            # More than one physical column can map to the same canonical name within one
            # entity (real POC 1 output does this — see the class docstring's second
            # implementation note). Highest confidence wins, deliberately rather than silently.
            best = max(matches, key=lambda f: f.confidence)
            return FieldLocation(entity=e.name, source_file=best.source_file, source_column=best.source_column)
        raise KeyError(f'no entity named "{entity}" in the semantic model')

    def resolve_field_by_source_column(self, entity: str, canonical_field: str, source_column: str) -> FieldLocation:
        """Same as `resolve_field`, but for the one caller that needs to bypass the
        highest-confidence tie-break above and pick a SPECIFIC physical column instead (found via
        the full-scale real-Olist `repeat_purchase_rate` finding — see `MetricDefinition`'s
        `group_by_source_column` docstring for why the tie-break itself is wrong for that metric,
        not just imprecise). Never used for planner-facing dimension/metric resolution — only for
        a HAVING_RATIO metric's own `group_by_field`, which is fixed at registry-configuration
        time and never chosen by the LLM."""
        for e in self.semantic_model.entities:
            if e.name != entity:
                continue
            matches = [f for f in e.fields if f.name == canonical_field and f.source_column == source_column]
            if not matches:
                raise KeyError(
                    f'entity "{entity}" has no field named "{canonical_field}" with source_column '
                    f'"{source_column}"'
                )
            best = matches[0]
            return FieldLocation(entity=e.name, source_file=best.source_file, source_column=best.source_column)
        raise KeyError(f'no entity named "{entity}" in the semantic model')

    def find_entity_for_field(self, canonical_field: str) -> str:
        """For a bare canonical field name with no entity supplied (an AST `dimension` or
        `having.field`), find which entity it belongs to. Raises if it's not found anywhere, or
        if it's ambiguous across more than one entity — a caller that already knows the entity
        should call `resolve_field(entity, field)` directly instead."""
        matches = [e.name for e in self.semantic_model.entities if any(f.name == canonical_field for f in e.fields)]
        if not matches:
            raise KeyError(f'"{canonical_field}" is not a known field in any entity of the semantic model')
        if len(matches) > 1:
            raise ValueError(
                f'"{canonical_field}" exists in more than one entity ({matches}) — ambiguous '
                "without an explicit entity; callers that know the entity should resolve_field() directly"
            )
        return matches[0]

    def resolve_join_path(self, from_entity: str, to_entity: str) -> Relationship | None:
        for rel in self.semantic_model.relationships:
            rel_from_entity, _ = self._parse_field_ref(rel.from_field)
            rel_to_entity, _ = self._parse_field_ref(rel.to_field)
            if {rel_from_entity, rel_to_entity} == {from_entity, to_entity}:
                return rel
        return None

    def _parse_field_ref(self, ref: str) -> tuple[str, str]:
        """`"orders.customer_id"` -> `("orders", "customer_id")`. POC 1's actual output shape,
        not ours to redesign — see semantic_model.py's vendoring note."""
        entity, sep, column = ref.partition(".")
        if not sep:
            raise ValueError(f'expected "entity.column", got {ref!r}')
        return entity, column
