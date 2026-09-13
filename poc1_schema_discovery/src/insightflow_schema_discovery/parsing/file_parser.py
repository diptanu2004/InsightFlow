from pathlib import Path

import pandas as pd


class FileParser:
    """Parses CSV/Excel into DataFrames. Stateless — swapping in DB/API sources later
    just means adding a new method here, not touching anything downstream."""

    SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}

    @staticmethod
    def parse(filepath: str) -> pd.DataFrame:
        path = Path(filepath)
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return pd.read_csv(path)
        if suffix in {".xlsx", ".xls"}:
            return pd.read_excel(path)
        raise ValueError(f"Unsupported file type: {suffix}")

    @classmethod
    def parse_all(cls, filepaths: list[str]) -> dict[str, pd.DataFrame]:
        """Keyed by file stem (e.g. 'orders.csv' -> 'orders'), used as the dataset name
        throughout the pipeline."""
        return {Path(fp).stem: cls.parse(fp) for fp in filepaths}
