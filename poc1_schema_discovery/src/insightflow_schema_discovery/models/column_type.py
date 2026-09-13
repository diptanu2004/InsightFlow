from enum import Enum


class ColumnType(str, Enum):
    IDENTIFIER = "identifier"
    DATE = "date"
    NUMERIC_CONTINUOUS = "numeric_continuous"
    NUMERIC_CURRENCY = "numeric_currency"
    CATEGORICAL = "categorical"
    TEXT = "text"
    BOOLEAN = "boolean"
