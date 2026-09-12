import re

import pandas as pd

from insightflow.models.column_profile import ColumnProfile

_ID_NAME_PATTERN = re.compile(r"(^id$|_id$|^id_|code$|_code$)", re.IGNORECASE)
_DATE_NAME_PATTERN = re.compile(r"(date|_dt$|^dt_|time)", re.IGNORECASE)
_CURRENCY_NAME_PATTERN = re.compile(r"(amt|amount|price|revenue|value|total|cost|net_)", re.IGNORECASE)


class DataProfiler:
    """Computes descriptive metadata per column. Fully deterministic — no LLM calls here.
    This is the only thing the semantic mapper is allowed to see for each column."""

    def profile(self, df: pd.DataFrame) -> list[ColumnProfile]:
        return [self._profile_column(df[col]) for col in df.columns]

    def _profile_column(self, series: pd.Series) -> ColumnProfile:
        n = len(series)
        null_pct = float(series.isna().mean()) if n else 0.0
        non_null = series.dropna()
        unique_count = int(non_null.nunique())
        cardinality_ratio = unique_count / n if n else 0.0
        is_numeric = pd.api.types.is_numeric_dtype(series)

        min_value = max_value = mean = stdev = None
        if is_numeric and not non_null.empty:
            min_value = float(non_null.min())
            max_value = float(non_null.max())
            mean = float(non_null.mean())
            stdev = float(non_null.std()) if len(non_null) > 1 else 0.0

        return ColumnProfile(
            column_name=str(series.name),
            dtype_raw=str(series.dtype),
            null_pct=round(null_pct, 4),
            unique_count=unique_count,
            cardinality_ratio=round(cardinality_ratio, 4),
            min_value=min_value,
            max_value=max_value,
            mean=round(mean, 4) if mean is not None else None,
            stdev=round(stdev, 4) if stdev is not None else None,
            sample_values=non_null.head(5).tolist(),
            looks_like_id=self._looks_like_id(str(series.name), cardinality_ratio),
            looks_like_date=self._looks_like_date(str(series.name), series),
            looks_like_categorical=(not is_numeric) and cardinality_ratio < 0.5,
            looks_like_currency=is_numeric and bool(_CURRENCY_NAME_PATTERN.search(str(series.name))),
        )

    @staticmethod
    def _looks_like_id(name: str, cardinality_ratio: float) -> bool:
        return bool(_ID_NAME_PATTERN.search(name)) or cardinality_ratio > 0.9

    @staticmethod
    def _looks_like_date(name: str, series: pd.Series) -> bool:
        if _DATE_NAME_PATTERN.search(name):
            return True
        sample = series.dropna().astype(str).head(10)
        if sample.empty:
            return False
        parsed = pd.to_datetime(sample, errors="coerce")
        return parsed.notna().mean() > 0.8
