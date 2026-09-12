from insightflow.inference.type_inferer import TypeInferer
from insightflow.models.column_profile import ColumnProfile


def _profile(**overrides) -> ColumnProfile:
    base = dict(
        column_name="col",
        dtype_raw="object",
        null_pct=0.0,
        unique_count=5,
        cardinality_ratio=0.5,
    )
    base.update(overrides)
    return ColumnProfile(**base)


def test_infers_identifier():
    p = _profile(looks_like_id=True)
    assert TypeInferer().infer(p).value == "identifier"


def test_infers_date():
    p = _profile(looks_like_date=True)
    assert TypeInferer().infer(p).value == "date"


def test_infers_currency():
    p = _profile(dtype_raw="float64", looks_like_currency=True)
    assert TypeInferer().infer(p).value == "numeric_currency"


def test_infers_numeric_continuous():
    p = _profile(dtype_raw="float64")
    assert TypeInferer().infer(p).value == "numeric_continuous"


def test_infers_categorical():
    p = _profile(dtype_raw="object", looks_like_categorical=True, cardinality_ratio=0.1)
    assert TypeInferer().infer(p).value == "categorical"


def test_defaults_to_text():
    p = _profile(dtype_raw="object", cardinality_ratio=0.9, looks_like_categorical=False)
    assert TypeInferer().infer(p).value == "text"
