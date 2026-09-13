"""Redirect the whole test process at a separate Postgres database *and* a separate Redis database.

Why this file exists at the rootdir level rather than inside `tests/`: `db/session.py` builds its
engine at *module import* time from `settings.database_url`, and `tests/infra.py` evaluates
`requires_postgres` (a `skipif` mark) at import time too. So the redirection has to happen before
anything imports `insightflow_backend.config` at all. pytest imports the rootdir conftest before
`tests/conftest.py`, and this module deliberately imports nothing from the app, so there is no
import-ordering hazard to get wrong later.

Why Postgres: `tests/db/conftest.py`'s `db_engine` fixture calls `Base.metadata.create_all()` /
`drop_all()` on whatever `DATABASE_URL` points at. Pointed at the dev database, every test run
destroyed dev data -- and because `alembic_version` isn't part of `Base.metadata`, `drop_all()`
left it behind still claiming head, so the next dev request failed with
`relation "users" does not exist` while `alembic current` insisted the schema was applied.

Why Redis too -- isolating Postgres alone made things worse, not better: tests and any locally
running dev worker then shared one RQ queue but *different* databases. A dev worker would dequeue
a test's discovery job, look for its Job row in the dev database, not find it, and return early
-- which RQ records as a successful "Job OK" in milliseconds. The test's own in-process worker
then drained an empty queue and the job sat at `pending` forever. Separately, `reset_rate_limits()`
deletes every `ratelimit:*` key it finds, so on a shared Redis database each test run also wiped
the dev server's live rate-limit buckets. Both `build_redis_client()` and `build_rq_connection()`
read `settings.redis_url`, so one redirect isolates the queue, rate limits and result cache.

Override with `TEST_DATABASE_URL` / `TEST_REDIS_URL`. Defaults: the dev database name with `_test`
appended (created automatically if missing), and Redis logical database 15 on the dev server.
"""
import os
import re
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

DEFAULT_DEV_DATABASE_URL = "postgresql+psycopg://insightflow:insightflow@localhost:5432/insightflow"
DEFAULT_DEV_REDIS_URL = "redis://localhost:6379/0"
# The highest logical database a default Redis server exposes (it ships with 16, 0-15). Dev uses 0
# everywhere -- config.py's default, docker-compose.yml, and .env.example.
TEST_REDIS_DB = 15


def _test_database_url(dev: str) -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    # Rebuild through urlsplit rather than string-concatenating, so a URL carrying a query string
    # (`?sslmode=require` on a managed Postgres) still gets the suffix on the database name.
    parts = urlsplit(dev)
    return urlunsplit(parts._replace(path=parts.path + "_test"))


def _test_redis_url(dev: str) -> str:
    explicit = os.environ.get("TEST_REDIS_URL")
    if explicit:
        return explicit
    parts = urlsplit(dev)
    return urlunsplit(parts._replace(path=f"/{TEST_REDIS_DB}"))


def _ensure_database_exists(url_string: str) -> None:
    """Create the test database if it isn't there, so a fresh clone doesn't need a manual
    `createdb` step. Silent no-op when Postgres isn't running -- `requires_postgres` skips those
    tests, and a missing-infra skip is clearer than an error raised from conftest import.
    """
    url = make_url(url_string)
    database = url.database
    # CREATE DATABASE can't take a bound parameter for the identifier, so refuse anything that
    # isn't a plain name rather than interpolating it into DDL.
    if not database or not re.fullmatch(r"[A-Za-z0-9_]+", database):
        return

    # `postgres` is the maintenance database every server has; CREATE DATABASE can't run inside
    # a transaction, hence AUTOCOMMIT.
    admin_engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            already_there = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": database}
            ).scalar()
            if not already_there:
                connection.execute(text(f'CREATE DATABASE "{database}"'))
    except OperationalError:
        return
    finally:
        admin_engine.dispose()


load_dotenv()

_dev_database_url = os.environ.get("DATABASE_URL", DEFAULT_DEV_DATABASE_URL)
_dev_redis_url = os.environ.get("REDIS_URL", DEFAULT_DEV_REDIS_URL)
_test_db = _test_database_url(_dev_database_url)
_test_redis = _test_redis_url(_dev_redis_url)

# The entire point of this file is that tests never touch dev state, so an override that points
# back at it (TEST_DATABASE_URL == DATABASE_URL, or dev already on Redis db 15) must fail loudly
# rather than silently reintroducing the data loss / stolen-job bugs described above.
if _test_db == _dev_database_url or _test_redis == _dev_redis_url:
    raise RuntimeError(
        "Test infrastructure resolves to the same target as dev "
        f"(database: {_test_db == _dev_database_url}, redis: {_test_redis == _dev_redis_url}). "
        "Set TEST_DATABASE_URL / TEST_REDIS_URL to something distinct."
    )

# Overwrite rather than setdefault: config.py's own load_dotenv() won't override an already-set
# environment variable, so this is what every engine and Redis client in the process binds to.
os.environ["DATABASE_URL"] = _test_db
os.environ["REDIS_URL"] = _test_redis
_ensure_database_exists(_test_db)
