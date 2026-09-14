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


def test_a_sparse_trailing_tail_does_not_become_the_anchor():
    """Real Olist: ~6,500 orders a month, then 16 in September 2018 and 4 in October."""
    from datetime import date, timedelta

    from insightflow_chatbot.services.date_bounds import anchor_date

    dates = []
    for month in range(3, 9):  # March..August, ~daily volume
        start = date(2018, month, 1)
        dates += [start + timedelta(days=i % 28) for i in range(6500)]
    dates += [date(2018, 9, 3)] * 16 + [date(2018, 10, 17)] * 4

    assert anchor_date(dates) == date(2018, 8, 28)


def test_normal_month_to_month_variation_and_growth_keep_the_real_latest_date():
    from datetime import date

    from insightflow_chatbot.services.date_bounds import anchor_date

    # A young business growing fast, and a real 50% drop, are both activity, not a ragged export.
    growing = [date(2024, m, 1) for m in range(1, 5) for _ in range(10 * 2**m)]
    dropping = [date(2024, m, 5) for m in range(1, 5) for _ in range(1000)] + [date(2024, 5, 20)] * 500
    assert anchor_date(growing) == date(2024, 4, 1)
    assert anchor_date(dropping) == date(2024, 5, 20)


def test_too_little_history_to_judge_leaves_the_latest_date_alone():
    from datetime import date

    from insightflow_chatbot.services.date_bounds import anchor_date

    dates = [date(2024, 1, 10)] * 500 + [date(2024, 2, 10)] * 500 + [date(2024, 3, 2)]
    assert anchor_date(dates) == date(2024, 3, 2)
