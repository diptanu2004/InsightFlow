"""Central config, loaded once from environment / .env. Import `settings` everywhere else.

No LLM-related settings here on purpose — see pyproject.toml's comment. If this file grows a
groq_api_key or similar, that's a sign POC 2 scope has drifted (hld.md: "No LLM dependency").
"""
import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    duckdb_path: str = os.getenv("DUCKDB_PATH", ":memory:")
    max_row_limit: int = int(os.getenv("MAX_ROW_LIMIT", "1000"))
    query_timeout_seconds: int = int(os.getenv("QUERY_TIMEOUT_SECONDS", "10"))
    data_dir: str = os.getenv("DATA_DIR", "data/raw/sample")


settings = Settings()
