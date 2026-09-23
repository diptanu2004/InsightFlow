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


def _single_entity_model(column="order_date"):
    from insightflow_core.models import SemanticModel

    return SemanticModel(
        entities=[{"name": "sales", "fields": [{"name": "transaction_date", "source_column": column, "source_file": "sales", "confidence": 0.9}]}],
        relationships=[],
    )


def test_us_formatted_dates_read_the_way_the_engine_reads_them(tmp_path):
    """Superstore exports "11/8/2017". fromisoformat rejected it, which would have broken chat for the whole
    dataset while the engine (DuckDB) read the same column fine."""
    rows = "".join(f"O{i},{m}/{d}/2017\n" for i, (m, d) in enumerate([(m, d) for m in range(1, 13) for d in (3, 17)]))
    (tmp_path / "sales.csv").write_text("order_id,order_date\n" + rows)

    assert infer_date_bounds(_single_entity_model(), str(tmp_path), None, "transaction_date") == (date(2017, 1, 3), date(2017, 12, 17))


def test_a_column_that_does_not_read_as_dates_is_reported_as_such(tmp_path):
    import pytest

    from insightflow_chatbot.services.date_bounds import UnreadableDates

    (tmp_path / "sales.csv").write_text("order_id,order_date\nO1,2017-01-05\nO2,sometime in March\nO3,2017-02-01\n")

    with pytest.raises(UnreadableDates, match='"order_date" column doesn\'t read as dates'):
        infer_date_bounds(_single_entity_model(), str(tmp_path), None, "transaction_date")


def test_a_long_gradual_tail_is_trimmed_too():
    """Real BikeStores: orders stop in April 2018, then dribble on for seven more months.

    Against the six months immediately before it, December's single order sat beside a median of
    1.5 and cleared the 10% bar on the first iteration, so the whole tail survived and the anchor
    landed on 2018-12-28 -- "last quarter" then meant a quarter holding four orders.
    """
    from datetime import date

    from insightflow_chatbot.services.date_bounds import anchor_date

    dates = [date(y, m, 15) for y in (2016, 2017) for m in range(1, 13) for _ in range(50)]
    dates += [date(2018, 1, 15)] * 52 + [date(2018, 2, 15)] * 35 + [date(2018, 3, 15)] * 68
    dates += [date(2018, 4, 1)] * 124 + [date(2018, 4, 27)]
    for month, count in ((6, 1), (7, 4), (8, 2), (9, 1), (10, 1), (11, 2), (12, 1)):
        dates += [date(2018, month, 20)] * count

    assert anchor_date(dates) == date(2018, 4, 27)


def test_a_us_datetime_column_reads_as_dates_instead_of_disabling_periods(tmp_path):
    """Real ClassicModels ORDERDATE: `2/24/2003 0:00`, which DuckDB's sniffer leaves as VARCHAR.

    The whole dataset lost every period question while all-time totals kept working
    (frontend/docs/hld.md finding 12).
    """
    from datetime import date

    rows = "".join(f"O{i},{m}/24/2003 0:00\n" for i, m in enumerate(range(1, 13)))
    (tmp_path / "sales.csv").write_text("order_id,order_date\n" + rows, encoding="utf-8")

    assert infer_date_bounds(_single_entity_model(), str(tmp_path), None, "transaction_date") == (
        date(2003, 1, 24),
        date(2003, 12, 24),
    )
