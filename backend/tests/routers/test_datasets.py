"""GET /projects/{project_id}/datasets (Phase 8 M3).

Deliberately does not go through POST schema/discover: that needs a real Groq call and a running
RQ worker, and is already covered end to end by tests/test_end_to_end_smoke.py. These tests care
about the listing contract -- ordering, scoping, and that a stored semantic model still validates
against the now-typed DatasetOut.semantic_model -- so Dataset rows are written directly.
"""
import uuid

from insightflow_backend.db.models import Dataset
from insightflow_backend.db.session import SessionLocal
from tests.routers.conftest import requires_postgres

PASSWORD = "correct-horse-battery-staple"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _register(client, email):
    r = client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _org_and_project(client, token, suffix):
    org = client.post("/organizations", json={"name": f"Acme {suffix}"}, headers=_auth(token))
    assert org.status_code == 201, org.text
    project = client.post(
        f"/organizations/{org.json()['id']}/projects",
        json={"name": f"Project {suffix}"},
        headers=_auth(token),
    )
    assert project.status_code == 201, project.text
    return org.json(), project.json()


def _semantic_model(field_name: str, confidence: float) -> dict:
    return {
        "entities": [
            {
                "name": "orders",
                "fields": [
                    {
                        "name": field_name,
                        "source_column": "net_amt",
                        "source_file": "orders",
                        "confidence": confidence,
                    }
                ],
            }
        ],
        "relationships": [],
    }


@requires_postgres
def test_datasets_is_empty_for_a_project_with_no_uploads(client):
    token = _register(client, "owner-empty@example.com")
    _, project = _org_and_project(client, token, "empty")

    r = client.get(f"/projects/{project['id']}/datasets", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json() == []


@requires_postgres
def test_non_member_cannot_list_datasets(client):
    owner_token = _register(client, "owner-scoped@example.com")
    _, project = _org_and_project(client, owner_token, "scoped")
    outsider_token = _register(client, "outsider@example.com")

    r = client.get(f"/projects/{project['id']}/datasets", headers=_auth(outsider_token))
    assert r.status_code == 403


@requires_postgres
def test_unknown_project_is_404(client):
    token = _register(client, "owner-404@example.com")
    r = client.get(f"/projects/{uuid.uuid4()}/datasets", headers=_auth(token))
    assert r.status_code == 404


@requires_postgres
def test_datasets_are_newest_first_and_scoped_to_their_project(committing_client):
    """Uses the committing fixture because the rows are inserted on a different connection than
    the request handler reads from -- the same constraint the async-job tests hit (see
    tests/infra.py's committing_db_client docstring).
    """
    suffix = uuid.uuid4().hex[:8]
    token = _register(committing_client, f"owner-{suffix}@example.com")
    org, project = _org_and_project(committing_client, token, suffix)
    _, other_project = _org_and_project(committing_client, token, f"{suffix}-other")
    user_id = uuid.UUID(committing_client.get("/auth/me", headers=_auth(token)).json()["id"])

    session = SessionLocal()
    try:
        for name, field, confidence in [
            ("first upload", "revenue", 0.91),
            ("second upload", "net_revenue", 0.42),
        ]:
            session.add(
                Dataset(
                    project_id=uuid.UUID(project["id"]),
                    name=name,
                    storage_prefix=f"{org['id']}/{project['id']}/{name}",
                    semantic_model=_semantic_model(field, confidence),
                    created_by=user_id,
                )
            )
            session.commit()  # separate commits so created_at ordering is unambiguous
        session.add(
            Dataset(
                project_id=uuid.UUID(other_project["id"]),
                name="someone else's upload",
                storage_prefix="x",
                semantic_model=_semantic_model("revenue", 0.9),
                created_by=user_id,
            )
        )
        session.commit()
    finally:
        session.close()

    r = committing_client.get(f"/projects/{project['id']}/datasets", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()

    assert [d["name"] for d in body] == ["second upload", "first upload"], "newest first"
    # The other project's dataset must not leak in, even though the same user owns both.
    assert all(d["project_id"] == project["id"] for d in body)
    # semantic_model is a typed SemanticModel now, not an opaque dict -- a stored model that
    # didn't validate would surface as a 500 here rather than passing through.
    assert body[0]["semantic_model"]["entities"][0]["fields"][0]["confidence"] == 0.42
    assert body[0]["semantic_model"]["relationships"] == []
