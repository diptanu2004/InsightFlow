"""Checks that mutating routes actually write an AuditLog row (not just that the mutation
itself succeeds) -- see insightflow_backend/audit.py.

Uses its own fixture (not the shared `client`/`real_db_client`) so the test can also hold a
reader Session bound to the exact same Connection every request's session uses -- reading back
via a brand-new `create_engine()` here hung (a separate connection blocking on a lock held by
this test's own still-open transaction), so the reader must share the connection, not just the
database.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from insightflow_backend.db.models import AuditLog
from insightflow_backend.db.session import get_db
from insightflow_backend.main import create_app
from tests.db.conftest import db_engine  # noqa: F401 -- reused fixture
from tests.infra import requires_postgres


@pytest.fixture
def client_and_db(db_engine):  # noqa: F811
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

    reader = SessionLocal()
    with TestClient(app) as test_client:
        yield test_client, reader
    reader.close()

    if transaction.is_active:
        transaction.rollback()
    connection.close()


def _register(client, email):
    r = client.post("/auth/register", json={"email": email, "password": "correct-horse-battery-staple"})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@requires_postgres
def test_create_organization_writes_audit_log(client_and_db):
    client, db = client_and_db
    token = _register(client, "owner@example.com")
    org = client.post("/organizations", json={"name": "Acme"}, headers=_auth(token)).json()

    entries = db.query(AuditLog).filter(AuditLog.org_id == org["id"]).all()
    assert any(e.action == "organization.created" for e in entries)


@requires_postgres
def test_add_member_writes_audit_log(client_and_db):
    client, db = client_and_db
    owner_token = _register(client, "owner@example.com")
    org = client.post("/organizations", json={"name": "Acme"}, headers=_auth(owner_token)).json()
    _register(client, "viewer@example.com")
    client.post(
        f"/organizations/{org['id']}/members",
        json={"email": "viewer@example.com", "role": "viewer"},
        headers=_auth(owner_token),
    )

    entries = db.query(AuditLog).filter(AuditLog.org_id == org["id"], AuditLog.action == "member.added").all()
    assert len(entries) == 1
    assert entries[0].detail == "role=viewer"


@requires_postgres
def test_create_project_writes_audit_log(client_and_db):
    client, db = client_and_db
    token = _register(client, "owner@example.com")
    org = client.post("/organizations", json={"name": "Acme"}, headers=_auth(token)).json()
    project = client.post(f"/organizations/{org['id']}/projects", json={"name": "P"}, headers=_auth(token)).json()

    entries = db.query(AuditLog).filter(AuditLog.resource_id == project["id"]).all()
    assert any(e.action == "project.created" for e in entries)


@requires_postgres
def test_delete_project_writes_audit_log(client_and_db):
    client, db = client_and_db
    token = _register(client, "owner@example.com")
    org = client.post("/organizations", json={"name": "Acme"}, headers=_auth(token)).json()
    project = client.post(f"/organizations/{org['id']}/projects", json={"name": "P"}, headers=_auth(token)).json()
    client.delete(f"/projects/{project['id']}", headers=_auth(token))

    entries = (
        db.query(AuditLog)
        .filter(AuditLog.resource_id == project["id"], AuditLog.action == "project.deleted")
        .all()
    )
    assert len(entries) == 1
