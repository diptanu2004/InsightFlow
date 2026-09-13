"""Central config, loaded once from environment / .env. Import `settings` everywhere else.

Combines POC 1's LLM settings (groq_api_key/groq_model, unmodified) with a small set of new
POC 4-only knobs. Unlike POC 2 (no LLM by design), an LLM dependency here is correct — hld.md
names QuestionPlanner and InsightGenerator as POC 4's two LLM calls. Engine-level settings
(duckdb_path, max_row_limit, query_timeout_seconds) live where insightflow_core's
`build_pipeline()` takes them as keyword arguments (see that function's own docstring for why
insightflow_core has no config module of its own) -- this file only holds the values, same as
POC 2/3's own config.py.
"""
import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # -- vendored from POC 1 (LLM) --
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # -- engine wiring, same defaults POC 2/3 have always used --
    duckdb_path: str = os.getenv("DUCKDB_PATH", ":memory:")
    max_row_limit: int = int(os.getenv("MAX_ROW_LIMIT", "1000"))
    query_timeout_seconds: int = int(os.getenv("QUERY_TIMEOUT_SECONDS", "10"))
    data_dir: str = os.getenv("DATA_DIR", "data/raw/sample")

    # -- new in POC 4 --
    # How many CategoryDelta rows a GROWTH_BY_DIMENSION answer surfaces to InsightGenerator's
    # prompt -- see class_diagram.md's resolved "ResultDiffer ranking convention" question.
    top_n_category_deltas: int = int(os.getenv("TOP_N_CATEGORY_DELTAS", "3"))


settings = Settings()
