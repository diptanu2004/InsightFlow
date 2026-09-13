"""Redirect the whole test process at a separate Postgres database.

Why this file exists at the repo-root level rather than inside `tests/`: `db/session.py` builds
its engine at *module import* time from `settings.database_url`, and `tests/infra.py` evaluates
`requires_postgres` (a `skipif` mark) at import time too. So the redirection has to happen before
anything imports `insightflow_backend.config` at all. pytest imports the rootdir conftest before
`tests/conftest.py`, and this module deliberately imports nothing from the app, so there is no
import-ordering hazard to get wrong later.

Why redirect at all: `tests/db/conftest.py`'s `db_engine` fixture calls
`Base.metadata.create_all()` / `drop_all()` on whatever `DATABASE_URL` points at. Pointed at the
dev database, every test run destroyed dev data -- and because `alembic_version` isn't part of
`Base.metadata`, `drop_all()` left it behind still claiming head, so the next dev request failed
with `relation "users" does not exist` while `alembic current` insisted the schema was applied.
Set `TEST_DATABASE_URL` to override; the default is the dev database name with `_test` appended.
"""
import os
import re
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

DEFAULT_DEV_DATABASE_URL = "postgresql+psycopg://insightflow:insightflow@localhost:5432/insightflow"


def _test_database_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    dev = os.environ.get("DATABASE_URL", DEFAULT_DEV_DATABASE_URL)
    # Rebuild through urlsplit rather than string-concatenating, so a URL carrying a query string
    # (`?sslmode=require` on a managed Postgres) still gets the suffix on the database name.
    parts = urlsplit(dev)
    return urlunsplit(parts._replace(path=parts.path + "_test"))


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
# Overwrite rather than setdefault: config.py's own load_dotenv() won't override an already-set
# environment variable, so this is what every engine in the test process ends up bound to.
os.environ["DATABASE_URL"] = _test_database_url()
_ensure_database_exists(os.environ["DATABASE_URL"])
