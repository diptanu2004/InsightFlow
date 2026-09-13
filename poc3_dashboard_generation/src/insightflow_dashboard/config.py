"""Central config, loaded once from environment / .env. Import `settings` everywhere else.

Combines POC 2's engine settings (duckdb/row-limit/timeout/data-dir, unmodified) with POC 1's
LLM settings (groq_api_key/groq_model, unmodified) plus a small set of new POC 3-only knobs for
the dashboard planner. Unlike POC 2, an LLM dependency here is correct, not scope drift — POC 3's
hld.md names DashboardPlanner as the one LLM call in the pipeline.
"""
import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # -- vendored from POC 2 (engine) --
    duckdb_path: str = os.getenv("DUCKDB_PATH", ":memory:")
    max_row_limit: int = int(os.getenv("MAX_ROW_LIMIT", "1000"))
    query_timeout_seconds: int = int(os.getenv("QUERY_TIMEOUT_SECONDS", "10"))
    data_dir: str = os.getenv("DATA_DIR", "data/raw/sample")

    # -- vendored from POC 1 (LLM) --
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # -- new in POC 3 --
    dashboard_min_components: int = int(os.getenv("DASHBOARD_MIN_COMPONENTS", "3"))
    dashboard_max_components: int = int(os.getenv("DASHBOARD_MAX_COMPONENTS", "8"))


settings = Settings()
