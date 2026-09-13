"""SemanticMapping.needs_confirmation must survive into the semantic model, not be dropped.

Phase 8 M4: _group_into_entities used to discard it, silently promoting unconfirmed guesses into the
model the analytics engine computes on -- the exact thing SemanticMapping's own docstring forbids.
"""
from insightflow_schema_discovery.models.semantic_mapping import SemanticMapping
from insightflow_schema_discovery.pipeline import SchemaDiscoveryPipeline


def test_needs_confirmation_is_carried_into_each_field_as_its_status():
    mappings = [
        SemanticMapping(source_column="payment_value", source_file="payments", semantic_type="revenue", confidence=0.98),
        SemanticMapping(
            source_column="freight_value",
            source_file="order_items",
            semantic_type="revenue",
            confidence=0.40,
            needs_confirmation=True,
        ),
    ]

    entities = {e.name: e for e in SchemaDiscoveryPipeline._group_into_entities(mappings)}

    assert entities["payments"].fields[0].status == "auto"
    assert entities["order_items"].fields[0].status == "needs_confirmation"


def test_status_survives_the_json_round_trip_the_backend_uses_to_bridge_models():
    # backend/wiring.py converts POC 1's SemanticModel into insightflow_core's via model_dump_json.
    from insightflow_schema_discovery.models.semantic_model import SemanticModel

    mappings = [
        SemanticMapping(
            source_column="seller_id", source_file="order_items", semantic_type="customer_id", confidence=0.55, needs_confirmation=True
        )
    ]
    model = SemanticModel(entities=SchemaDiscoveryPipeline._group_into_entities(mappings), relationships=[])

    restored = SemanticModel.model_validate_json(model.model_dump_json())

    assert restored.entities[0].fields[0].status == "needs_confirmation"
