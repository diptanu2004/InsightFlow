"""Central config, loaded once from environment / .env. Import `settings` everywhere else.

Union of every POC's own config.py (POC 1's LLM settings, POC 2/3/4's engine-wiring settings --
duckdb/row-limit/timeout, POC 3's dashboard-component knobs, POC 4's time-field/category-delta
knobs) plus Phase 6's Postgres/JWT/S3 settings for auth and multi-tenancy.
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

    # -- Phase 6: Postgres (app/tenant metadata -- users, orgs, projects, datasets) --
    # No default in prod: a missing DATABASE_URL should fail loudly at startup, not silently
    # fall back to something a developer forgot to point at a real database.
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://insightflow:insightflow@localhost:5432/insightflow"
    )

    # -- Phase 6: JWT auth --
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    jwt_algorithm: str = "HS256"
    jwt_access_token_ttl_minutes: int = int(os.getenv("JWT_ACCESS_TOKEN_TTL_MINUTES", "15"))
    jwt_refresh_token_ttl_days: int = int(os.getenv("JWT_REFRESH_TOKEN_TTL_DAYS", "30"))

    # -- Phase 6: object storage for uploaded datasets (S3-compatible; MinIO locally, S3 in prod) --
    # No local-disk backend: dev/test run against MinIO too, so the storage code path is
    # identical in every environment and only the endpoint URL differs (see docker-compose.yml).
    s3_endpoint_url: str = os.getenv("S3_ENDPOINT_URL", "http://localhost:9000")
    s3_bucket: str = os.getenv("S3_BUCKET", "insightflow-datasets")
    aws_access_key_id: str = os.getenv("AWS_ACCESS_KEY_ID", "insightflow")
    aws_secret_access_key: str = os.getenv("AWS_SECRET_ACCESS_KEY", "insightflow123")
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")
    # Per-process local cache for objects downloaded from S3 -- POC pipelines (DuckDB/pandas)
    # read local file paths, not S3 URIs. See storage.py.
    dataset_cache_dir: Path = Path(os.getenv("DATASET_CACHE_DIR", ".dataset_cache"))

    # -- Phase 6: production hardening --
    # Comma-separated list, fails closed (empty = no browser origin allowed) since there's no
    # frontend yet (Phase 8) -- an operator must opt in explicitly once one exists, rather than
    # the API silently accepting cross-origin requests from anywhere by default.
    cors_allowed_origins: list[str] = [
        o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
    ]
    # In-memory stopgap rate limit on /auth/login and /auth/register only -- per-process, not
    # distributed, resets on restart. Real rate limiting (Redis-backed, all routes) is Phase 7;
    # this exists only so a bare deployment isn't wide open to unlimited login/registration
    # attempts in the meantime. See auth/rate_limit.py.
    auth_rate_limit_max_requests: int = int(os.getenv("AUTH_RATE_LIMIT_MAX_REQUESTS", "10"))
    auth_rate_limit_window_seconds: float = float(os.getenv("AUTH_RATE_LIMIT_WINDOW_SECONDS", "60"))


settings = Settings()
