"""Real pipelines, real DuckDB, real S3-compatible storage, real Postgres -- same "no mocks"
convention every POC's own integration tests already use. Phase 6 replaced the single global
SessionState this test used to exercise directly with real auth + multi-tenancy, so the smoke
test now goes through the same register -> login -> create org -> create project -> upload flow
a real client would, per the Phase 6 plan's M5 milestone.
"""
import os

import pytest

from tests.conftest import SAMPLE_DATA_DIR
from tests.infra import requires_infra, requires_postgres

requires_groq = pytest.mark.skipif(not os.getenv("GROQ_API_KEY"), reason="requires a real GROQ_API_KEY")


def _register_and_login(client, email="smoke@example.com"):
    r = client.post("/auth/register", json={"email": email, "password": "correct-horse-battery-staple"})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _create_project(client, token, org_name="SmokeCo", project_name="Smoke Project"):
    org = client.post("/organizations", json={"name": org_name}, headers=_auth(token)).json()
    project = client.post(
        f"/organizations/{org['id']}/projects", json={"name": project_name}, headers=_auth(token)
    ).json()
    return project


def _upload_sample_files(client, token, project_id):
    files = [
        ("files", ("customers.csv", open(SAMPLE_DATA_DIR / "customers.csv", "rb"), "text/csv")),
        ("files", ("orders.csv", open(SAMPLE_DATA_DIR / "orders.csv", "rb"), "text/csv")),
        ("files", ("products.csv", open(SAMPLE_DATA_DIR / "products.csv", "rb"), "text/csv")),
    ]
    return client.post(f"/projects/{project_id}/schema/discover", files=files, headers=_auth(token))


@requires_postgres
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@requires_postgres
def test_query_before_schema_discovery_returns_409(client):
    token = _register_and_login(client)
    project = _create_project(client, token)
    r = client.post(
        f"/projects/{project['id']}/analytics/query",
        json={"operation": "aggregate", "metric": "revenue"},
        headers=_auth(token),
    )
    assert r.status_code == 409


@requires_postgres
def test_dashboard_before_schema_discovery_returns_409(client):
    token = _register_and_login(client)
    project = _create_project(client, token)
    r = client.post(f"/projects/{project['id']}/dashboard/generate", headers=_auth(token))
    assert r.status_code == 409


@requires_postgres
def test_chat_before_schema_discovery_returns_409(client):
    token = _register_and_login(client)
    project = _create_project(client, token)
    r = client.post(
        f"/projects/{project['id']}/chat/ask", json={"question": "What was total revenue?"}, headers=_auth(token)
    )
    assert r.status_code == 409


@requires_postgres
def test_schema_discover_rejects_non_csv(client):
    token = _register_and_login(client)
    project = _create_project(client, token)
    files = [("files", ("orders.xlsx", b"not really excel", "application/vnd.ms-excel"))]
    r = client.post(f"/projects/{project['id']}/schema/discover", files=files, headers=_auth(token))
    assert r.status_code == 400


@requires_postgres
def test_viewer_cannot_upload_dataset(client):
    owner_token = _register_and_login(client, email="owner@example.com")
    org = client.post("/organizations", json={"name": "ViewerCo"}, headers=_auth(owner_token)).json()
    project = client.post(
        f"/organizations/{org['id']}/projects", json={"name": "P"}, headers=_auth(owner_token)
    ).json()

    viewer_token = _register_and_login(client, email="viewer@example.com")
    client.post(
        f"/organizations/{org['id']}/members",
        json={"email": "viewer@example.com", "role": "viewer"},
        headers=_auth(owner_token),
    )

    files = [("files", ("orders.csv", b"id,amount\n1,10\n", "text/csv"))]
    r = client.post(f"/projects/{project['id']}/schema/discover", files=files, headers=_auth(viewer_token))
    assert r.status_code == 403


@requires_infra
@requires_groq
def test_full_flow_against_sample_data(client):
    token = _register_and_login(client)
    project = _create_project(client, token)

    r = _upload_sample_files(client, token, project["id"])
    assert r.status_code == 201, r.text
    dataset = r.json()
    assert dataset["semantic_model"]["entities"]

    query = {"operation": "aggregate", "metric": "revenue"}
    r = client.post(f"/projects/{project['id']}/analytics/query", json=query, headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["shape"] == "scalar"

    r = client.post(f"/projects/{project['id']}/dashboard/generate", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["components"]

    r = client.post(
        f"/projects/{project['id']}/chat/ask", json={"question": "What was total revenue?"}, headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    assert "refused" in r.json()
