"""GET /projects/{project_id}/powerbi/export -- the route contract only (Phase 9).

What the export *contains* is covered by tests/test_powerbi_export.py against real CSVs; this file
is about the parts only the route decides: who may call it, which dataset gets exported, and that a
download leaves an audit trail.

Two deliberate fixture choices, both following existing precedent rather than inventing one:
  - Its own connection-sharing fixture, like tests/routers/test_audit_log.py. Reading back a row the
    request itself wrote needs a Session on the *same* connection; a fresh one blocks on the test's
    own open transaction.
  - `get_pipeline_cache` is overridden with a local directory, so this doesn't need MinIO. The S3
    transport underneath it is already verified against real MinIO in tests/test_storage.py.
"""
import io
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from insightflow_backend.dataset_deps import get_pipeline_cache
from insightflow_backend.db.models import AuditLog, Dataset
from insightflow_backend.db.session import get_db
from insightflow_backend.main import create_app
from tests.db.conftest import db_engine  # noqa: F401 -- reused fixture
from tests.infra import requires_postgres, reset_rate_limits, skip_if_redis_unavailable

PASSWORD = "correct-horse-battery-staple"
ORDERS_CSV = "order_id,customer_id,order_total,order_purchase_timestamp\no1,c1,100.0,2023-01-15\no2,c2,50.0,2023-02-20\n"


def _semantic_model() -> dict:
    field = lambda name, column, confidence: {  # noqa: E731
        "name": name,
        "source_column": column,
        "source_file": "orders",
        "confidence": confidence,
        "status": "auto",
    }
    return {
        "entities": [
            {
                "name": "orders",
                "fields": [
                    field("order_id", "order_id", 0.99),
                    field("customer_id", "customer_id", 0.95),
                    field("revenue", "order_total", 0.9),
                    field("transaction_date", "order_purchase_timestamp", 0.98),
                ],
            }
        ],
        "relationships": [],
    }


class _LocalCache:
    """Stands in for PipelineCache: the export only ever asks it for a dataset's local data dir."""

    def __init__(self, data_dir: Path):
        self._data_dir = data_dir

    def get_data_dir(self, dataset) -> Path:
        return self._data_dir


@pytest.fixture
def exporting(db_engine, tmp_path):  # noqa: F811
    skip_if_redis_unavailable()  # /auth/register is Redis-gated
    reset_rate_limits()  # every TestClient shares one fake IP
    (tmp_path / "orders.csv").write_text(ORDERS_CSV, encoding="utf-8")

    connection = db_engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection)

    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_pipeline_cache] = lambda: _LocalCache(tmp_path)

    reader = SessionLocal()
    with TestClient(app) as test_client:
        yield test_client, reader
    reader.close()

    if transaction.is_active:
        transaction.rollback()
    connection.close()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _register(client, prefix="owner"):
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    return r.json()["access_token"], email


def _org_and_project(client, token):
    org = client.post("/organizations", json={"name": "Acme"}, headers=_auth(token))
    assert org.status_code == 201, org.text
    project = client.post(
        f"/organizations/{org.json()['id']}/projects", json={"name": "P"}, headers=_auth(token)
    )
    assert project.status_code == 201, project.text
    return org.json(), project.json()


def _me(client, token) -> str:
    r = client.get("/auth/me", headers=_auth(token))
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _make_dataset(
    reader, project_id: str, user_id: str, name: str = "olist", created_at: datetime | None = None
) -> uuid.UUID:
    """`created_at` is explicit wherever a test cares which row is newest.

    `get_latest_dataset` orders by `created_at` alone, and two rows inserted back to back can carry
    the same timestamp -- which makes "the newest" genuinely arbitrary rather than wrong. Real
    uploads are seconds apart so this is a test artifact, but leaving it to chance made the ordering
    test pass alone and fail in a full run.
    """
    dataset_id = uuid.uuid4()
    reader.add(
        Dataset(
            id=dataset_id,
            created_at=created_at or datetime.now(timezone.utc),
            project_id=uuid.UUID(project_id),
            name=name,
            storage_prefix=f"org/{project_id}/{dataset_id}",
            semantic_model=_semantic_model(),
            created_by=uuid.UUID(user_id),
        )
    )
    reader.flush()
    return dataset_id


@requires_postgres
def test_analyst_downloads_a_power_bi_project(exporting):
    client, reader = exporting
    token, _ = _register(client)
    _, project = _org_and_project(client, token)
    _make_dataset(reader, project["id"], _me(client, token))

    response = client.get(f"/projects/{project['id']}/powerbi/export", headers=_auth(token))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    names = set(zipfile.ZipFile(io.BytesIO(response.content)).namelist())
    assert "olist.pbip" in names
    assert "data/orders.csv" in names
    assert "README.md" in names


@requires_postgres
def test_viewer_cannot_export(exporting):
    """A Viewer may read query results but may not walk away with the raw rows -- the one read route
    gated above Viewer, because none of the platform's protections survive the download."""
    client, reader = exporting
    owner_token, _ = _register(client)
    org, project = _org_and_project(client, owner_token)
    _make_dataset(reader, project["id"], _me(client, owner_token))

    viewer_token, viewer_email = _register(client, "viewer")
    added = client.post(
        f"/organizations/{org['id']}/members",
        json={"email": viewer_email, "role": "viewer"},
        headers=_auth(owner_token),
    )
    assert added.status_code in (200, 201), added.text

    response = client.get(f"/projects/{project['id']}/powerbi/export", headers=_auth(viewer_token))
    assert response.status_code == 403, response.text


@requires_postgres
def test_export_is_audit_logged(exporting):
    client, reader = exporting
    token, _ = _register(client)
    _, project = _org_and_project(client, token)
    dataset_id = _make_dataset(reader, project["id"], _me(client, token))

    assert client.get(f"/projects/{project['id']}/powerbi/export", headers=_auth(token)).status_code == 200

    entries = reader.query(AuditLog).filter(AuditLog.action == "dataset.powerbi_exported").all()
    assert [str(e.resource_id) for e in entries] == [str(dataset_id)]


@requires_postgres
def test_export_uses_the_newest_dataset(exporting):
    """"The" dataset for a project is its most recently created row -- a mapping review writes a new
    row rather than editing one, so an export after a review must pick up the reviewed model."""
    client, reader = exporting
    token, _ = _register(client)
    _, project = _org_and_project(client, token)
    user_id = _me(client, token)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _make_dataset(reader, project["id"], user_id, name="older", created_at=base)
    newest = _make_dataset(reader, project["id"], user_id, name="newer", created_at=base + timedelta(minutes=5))

    response = client.get(f"/projects/{project['id']}/powerbi/export", headers=_auth(token))
    assert response.status_code == 200, response.text
    assert "newer.pbip" in set(zipfile.ZipFile(io.BytesIO(response.content)).namelist())

    entry = reader.query(AuditLog).filter(AuditLog.action == "dataset.powerbi_exported").one()
    assert str(entry.resource_id) == str(newest)


@requires_postgres
def test_export_before_any_upload_is_409(exporting):
    client, _ = exporting
    token, _unused = _register(client)
    _, project = _org_and_project(client, token)
    response = client.get(f"/projects/{project['id']}/powerbi/export", headers=_auth(token))
    assert response.status_code == 409, response.text
