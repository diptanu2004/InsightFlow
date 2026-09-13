"""Central config, loaded once from environment / .env. Import `settings` everywhere else.

Union of every POC's own config.py: POC 1's LLM settings, POC 2/3/4's engine-wiring settings
(duckdb/row-limit/timeout), POC 3's dashboard-component knobs, POC 4's time-field/category-delta
knobs, plus one new Phase 5-only setting (upload_root) for where uploaded CSVs land.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # -- LLM (shared GroqLLMClient across schema discovery, dashboard, chat) --
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # -- engine wiring, same defaults every POC has always used --
    duckdb_path: str = os.getenv("DUCKDB_PATH", ":memory:")
    max_row_limit: int = int(os.getenv("MAX_ROW_LIMIT", "1000"))
    query_timeout_seconds: int = int(os.getenv("QUERY_TIMEOUT_SECONDS", "10"))

    # -- from POC 3 --
    dashboard_min_components: int = int(os.getenv("DASHBOARD_MIN_COMPONENTS", "3"))
    dashboard_max_components: int = int(os.getenv("DASHBOARD_MAX_COMPONENTS", "8"))

    # -- from POC 4 --
    time_entity: str = os.getenv("TIME_ENTITY", "orders")
    time_field: str = os.getenv("TIME_FIELD", "transaction_date")
    top_n_category_deltas: int = int(os.getenv("TOP_N_CATEGORY_DELTAS", "3"))

    # -- new in Phase 5: where /schema/discover writes uploaded CSVs, one subdir per upload --
    # Relative to cwd, same convention as every POC's own default data_dir ("data/raw/sample")
    # assuming the process is run from inside backend/ (e.g. `cd backend && uv run uvicorn ...`).
    upload_root: Path = Path(os.getenv("UPLOAD_ROOT", ".uploads"))


settings = Settings()
