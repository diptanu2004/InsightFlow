from insightflow_schema_discovery.models.column_profile import ColumnProfile
from insightflow_schema_discovery.models.column_type import ColumnType


class TypeInferer:
    """Rule-based classifier sitting on top of DataProfiler output. Deterministic on
    purpose — gives the LLM a stronger prior in the semantic mapping step."""

    def infer(self, profile: ColumnProfile) -> ColumnType:
        if profile.looks_like_id:
            return ColumnType.IDENTIFIER
        if profile.looks_like_date:
            return ColumnType.DATE
        if profile.looks_like_currency:
            return ColumnType.NUMERIC_CURRENCY
        if "float" in profile.dtype_raw or "int" in profile.dtype_raw:
            return ColumnType.NUMERIC_CONTINUOUS
        if profile.dtype_raw == "bool":
            return ColumnType.BOOLEAN
        if profile.looks_like_categorical:
            return ColumnType.CATEGORICAL
        return ColumnType.TEXT
