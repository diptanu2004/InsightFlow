from datetime import date

from insightflow.services.date_bounds import infer_date_bounds


def test_infer_date_bounds_matches_sample_dataset(semantic_model):
    from pathlib import Path

    data_dir = Path(__file__).parent.parent / "data" / "raw" / "sample"
    min_date, max_date = infer_date_bounds(semantic_model, str(data_dir), "orders", "transaction_date")
    assert min_date == date(2025, 11, 1)
    assert max_date == date(2026, 2, 15)
