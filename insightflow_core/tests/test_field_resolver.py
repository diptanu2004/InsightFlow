"""Unit tests for FieldResolver — in particular the entity-disambiguation behavior that forced
the resolve_field(entity, field) signature change from the original class_diagram.md sketch of
resolve_field(field) alone (see field_resolver.py's "Implementation note").
"""
import json
from pathlib import Path

import pytest

from insightflow_core.compilation import FieldResolver
from insightflow_core.models import Entity, SemanticField, SemanticModel

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture()
def resolver() -> FieldResolver:
    semantic_model = SemanticModel(**json.loads((DATA_DIR / "semantic_model.json").read_text()))
    return FieldResolver(semantic_model)


def test_resolve_field_within_named_entity(resolver):
    loc = resolver.resolve_field("orders", "revenue")
    assert loc.entity == "orders"
    assert loc.source_column == "revenue"
    assert loc.source_file == "orders"


def test_resolve_field_unknown_entity_raises(resolver):
    with pytest.raises(KeyError):
        resolver.resolve_field("not_an_entity", "revenue")


def test_resolve_field_unknown_field_in_known_entity_raises(resolver):
    with pytest.raises(KeyError):
        resolver.resolve_field("orders", "not_a_field")


def test_find_entity_for_field_unambiguous(resolver):
    assert resolver.find_entity_for_field("category") == "products"
    assert resolver.find_entity_for_field("region") == "customers"


def test_find_entity_for_field_ambiguous_raises():
    # The sample semantic model uses POC 1's real canonical names (customer_name/product_name
    # are distinct), so it has no collision to test against on its own — this mirrors the real
    # collision found via the Olist integration test instead: "region" mapped into both
    # customers and sellers.
    sm = SemanticModel(
        entities=[
            Entity(name="customers", fields=[SemanticField(name="region", source_column="state", source_file="customers", confidence=0.9)]),
            Entity(name="sellers", fields=[SemanticField(name="region", source_column="state", source_file="sellers", confidence=0.7)]),
        ],
        relationships=[],
    )
    with pytest.raises(ValueError):
        FieldResolver(sm).find_entity_for_field("region")


def test_resolve_field_prefers_highest_confidence_on_within_entity_collision():
    # Mirrors the real Olist case: customers.customer_id AND customers.customer_unique_id both
    # mapped to canonical "customer_id" -- resolve_field must not just take whichever came first.
    sm = SemanticModel(
        entities=[
            Entity(
                name="customers",
                fields=[
                    SemanticField(name="customer_id", source_column="customer_unique_id", source_file="customers", confidence=0.95),
                    SemanticField(name="customer_id", source_column="customer_id", source_file="customers", confidence=0.99),
                ],
            )
        ],
        relationships=[],
    )
    loc = FieldResolver(sm).resolve_field("customers", "customer_id")
    assert loc.source_column == "customer_id"  # the 0.99 one, even though it's listed second


def test_find_entity_for_field_unknown_raises(resolver):
    with pytest.raises(KeyError):
        resolver.find_entity_for_field("not_a_field_anywhere")


def test_resolve_join_path_finds_single_hop(resolver):
    rel = resolver.resolve_join_path("orders", "customers")
    assert rel is not None
    assert {resolver._parse_field_ref(rel.from_field)[0], resolver._parse_field_ref(rel.to_field)[0]} == {
        "orders",
        "customers",
    }


def test_resolve_join_path_no_relationship_returns_none(resolver):
    # customers <-> products have no direct relationship in the sample data (only via orders)
    assert resolver.resolve_join_path("customers", "products") is None


def test_parse_field_ref():
    from insightflow_core.models import SemanticModel as _SM

    r = FieldResolver(_SM(entities=[], relationships=[]))
    assert r._parse_field_ref("orders.customer_id") == ("orders", "customer_id")
    with pytest.raises(ValueError):
        r._parse_field_ref("no_dot_here")
