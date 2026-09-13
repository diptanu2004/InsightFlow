"""POST /projects/{project_id}/datasets/{dataset_id}/mapping-decisions (Phase 8).

Uses the committing fixture for the same reason test_datasets.py does: Dataset rows are written on a
different connection than the one the request handler reads from.
"""
import uuid

from insightflow_backend.db.models import AuditLog, Dataset
from insightflow_backend.db.session import SessionLocal
from tests.routers.conftest import requires_postgres

PASSWORD = "correct-horse-battery-staple"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _register(client, email):
    r = client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _field(name, column, file, confidence, status):
    return {"name": name, "source_column": column, "source_file": file, "confidence": confidence, "status": status}


def _model():
    # Shaped like the real Olist mis-mappings this flow exists for.
    return {
        "entities": [
            {
                "name": "order_items",
                "fields": [
                    _field("order_id", "order_id", "order_items", 0.99, "auto"),
                    _field("revenue", "freight_value", "order_items", 0.40, "needs_confirmation"),
                    _field("price", "price", "order_items", 0.60, "needs_confirmation"),
                ],
            }
        ],
        "relationships": [],
    }


def _setup(client, suffix, *, datasets=1):
    token = _register(client, f"analyst-{suffix}@example.com")
    org = client.post("/organizations", json={"name": f"Org {suffix}"}, headers=_auth(token)).json()
    project = client.post(
        f"/organizations/{org['id']}/projects", json={"name": f"P {suffix}"}, headers=_auth(token)
    ).json()
    user_id = uuid.UUID(client.get("/auth/me", headers=_auth(token)).json()["id"])
    ids = []
    session = SessionLocal()
    try:
        for i in range(datasets):
            row = Dataset(
                project_id=uuid.UUID(project["id"]),
                name="order_items",
                storage_prefix=f"{org['id']}/{project['id']}/v{i}",
                semantic_model=_model(),
                created_by=user_id,
            )
            session.add(row)
            session.commit()
            ids.append(str(row.id))
    finally:
        session.close()
    return token, org, project, ids


def _post(client, token, project_id, dataset_id, decisions):
    return client.post(
        f"/projects/{project_id}/datasets/{dataset_id}/mapping-decisions",
        json={"decisions": decisions},
        headers=_auth(token),
    )


@requires_postgres
def test_decisions_create_a_new_version_and_leave_the_original_untouched(committing_client):
    suffix = uuid.uuid4().hex[:8]
    token, org, project, (original_id,) = _setup(committing_client, suffix)

    r = _post(
        committing_client,
        token,
        project["id"],
        original_id,
        [
            {"source_file": "order_items", "source_column": "freight_value", "decision": "reject"},
            {"source_file": "order_items", "source_column": "price", "decision": "confirm"},
        ],
    )

    assert r.status_code == 201, r.text
    new_version = r.json()
    assert new_version["id"] != original_id
    statuses = {f["source_column"]: f["status"] for f in new_version["semantic_model"]["entities"][0]["fields"]}
    assert statuses == {"order_id": "auto", "freight_value": "rejected", "price": "confirmed"}

    listed = committing_client.get(f"/projects/{project['id']}/datasets", headers=_auth(token)).json()
    assert [d["id"] for d in listed] == [new_version["id"], original_id], "the reviewed version becomes the active one"
    original_statuses = {f["source_column"]: f["status"] for f in listed[1]["semantic_model"]["entities"][0]["fields"]}
    assert original_statuses["freight_value"] == "needs_confirmation"

    session = SessionLocal()
    try:
        entry = session.query(AuditLog).filter(AuditLog.action == "dataset.mappings_reviewed", AuditLog.org_id == org["id"]).one()
        assert "1 confirmed, 1 rejected" in entry.detail
    finally:
        session.close()


@requires_postgres
def test_deciding_against_an_older_version_is_refused(committing_client):
    token, _, project, (older, _newer) = _setup(committing_client, uuid.uuid4().hex[:8], datasets=2)
    r = _post(committing_client, token, project["id"], older, [{"source_file": "order_items", "source_column": "price", "decision": "confirm"}])
    assert r.status_code == 409


@requires_postgres
def test_a_viewer_cannot_review_mappings(committing_client):
    suffix = uuid.uuid4().hex[:8]
    owner_token, org, project, (dataset_id,) = _setup(committing_client, suffix)
    viewer_token = _register(committing_client, f"viewer-{suffix}@example.com")
    committing_client.post(
        f"/organizations/{org['id']}/members",
        json={"email": f"viewer-{suffix}@example.com", "role": "viewer"},
        headers=_auth(owner_token),
    )

    r = _post(committing_client, viewer_token, project["id"], dataset_id, [{"source_file": "order_items", "source_column": "price", "decision": "confirm"}])

    assert r.status_code == 403


@requires_postgres
def test_unknown_columns_duplicates_and_no_ops_are_rejected(committing_client):
    token, _, project, (dataset_id,) = _setup(committing_client, uuid.uuid4().hex[:8])

    unknown = _post(committing_client, token, project["id"], dataset_id, [{"source_file": "order_items", "source_column": "nope", "decision": "reject"}])
    assert unknown.status_code == 422 and "order_items.nope" in unknown.json()["detail"]

    duplicate = _post(
        committing_client,
        token,
        project["id"],
        dataset_id,
        [
            {"source_file": "order_items", "source_column": "price", "decision": "confirm"},
            {"source_file": "order_items", "source_column": "price", "decision": "reject"},
        ],
    )
    assert duplicate.status_code == 422

    # order_id is already "auto"; rejecting nothing new would be a pointless version.
    no_op = _post(committing_client, token, project["id"], dataset_id, [{"source_file": "order_items", "source_column": "order_id", "decision": "confirm"}])
    assert no_op.status_code == 201  # auto -> confirmed IS a change: it records a human's sign-off
    again = _post(committing_client, token, project["id"], no_op.json()["id"], [{"source_file": "order_items", "source_column": "order_id", "decision": "confirm"}])
    assert again.status_code == 422
