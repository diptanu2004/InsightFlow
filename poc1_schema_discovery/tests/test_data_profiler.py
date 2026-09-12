import pandas as pd

from insightflow.profiling.data_profiler import DataProfiler


def test_profiles_id_like_column():
    df = pd.DataFrame({"client": ["C1", "C2", "C3", "C4"]})
    profile = DataProfiler().profile(df)[0]
    assert profile.column_name == "client"
    assert profile.cardinality_ratio == 1.0


def test_detects_currency_like_column_by_name():
    df = pd.DataFrame({"net_amt": [10.0, 20.0, 30.0]})
    profile = DataProfiler().profile(df)[0]
    assert profile.looks_like_currency is True


def test_detects_date_like_column_by_values():
    df = pd.DataFrame({"purchase_dt": ["2024-01-01", "2024-01-02", "2024-01-03"]})
    profile = DataProfiler().profile(df)[0]
    assert profile.looks_like_date is True


def test_null_percentage_computed_correctly():
    df = pd.DataFrame({"col": [1, None, 3, None]})
    profile = DataProfiler().profile(df)[0]
    assert profile.null_pct == 0.5
