from datetime import date

from insightflow_chatbot.services.date_bounds import infer_date_bounds


def test_infer_date_bounds_matches_sample_dataset(semantic_model):
    from pathlib import Path

    data_dir = Path(__file__).parent.parent / "data" / "raw" / "sample"
    min_date, max_date = infer_date_bounds(semantic_model, str(data_dir), "orders", "transaction_date")
    assert min_date == date(2025, 11, 1)
    assert max_date == date(2026, 2, 15)


def test_time_entity_resolves_per_dataset_when_not_configured(semantic_model):
    """Phase 8 M4: a fixed TIME_ENTITY="orders" broke chat construction for any upload whose file
    wasn't literally named orders.csv. Unset, the entity is whichever one carries the time field."""
    from pathlib import Path

    data_dir = Path(__file__).parent.parent / "data" / "raw" / "sample"
    for entity in semantic_model.entities:
        if entity.name == "orders":
            entity.name = "olist_orders_dataset"  # source_file still points at orders.csv

    assert infer_date_bounds(semantic_model, str(data_dir), None, "transaction_date") == infer_date_bounds(
        semantic_model, str(data_dir), "olist_orders_dataset", "transaction_date"
    )


def test_time_field_on_more_than_one_entity_is_refused_not_guessed(semantic_model):
    import pytest

    from insightflow_core.models import SemanticField

    customers = next(e for e in semantic_model.entities if e.name == "customers")
    customers.fields.append(
        SemanticField(name="transaction_date", source_column="signup_date", source_file="customers", confidence=0.6)
    )

    with pytest.raises(ValueError, match="more than one entity"):
        infer_date_bounds(semantic_model, "unused", None, "transaction_date")
