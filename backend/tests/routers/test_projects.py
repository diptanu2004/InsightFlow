from tests.routers.conftest import requires_postgres


def _register(client, email):
    r = client.post("/auth/register", json={"email": email, "password": "correct-horse-battery-staple"})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _create_org(client, token, name="Acme"):
    r = client.post("/organizations", json={"name": name}, headers=_auth(token))
    assert r.status_code == 201, r.text
    return r.json()


def _add_member(client, owner_token, org_id, email, role):
    # Assumes `email` is already registered -- callers register the user themselves so they can
    # keep the returned access token for later use in the test.
    r = client.post(
        f"/organizations/{org_id}/members", json={"email": email, "role": role}, headers=_auth(owner_token)
    )
    assert r.status_code == 201, r.text


@requires_postgres
def test_owner_can_create_project(client):
    owner_token = _register(client, "owner@example.com")
    org = _create_org(client, owner_token)

    r = client.post(f"/organizations/{org['id']}/projects", json={"name": "Q3 Analytics"}, headers=_auth(owner_token))
    assert r.status_code == 201, r.text
    assert r.json()["org_id"] == org["id"]


@requires_postgres
def test_analyst_cannot_create_project(client):
    owner_token = _register(client, "owner@example.com")
    org = _create_org(client, owner_token)
    analyst_token = _register(client, "analyst@example.com")
    _add_member(client, owner_token, org["id"], "analyst@example.com", "analyst")

    r = client.post(
        f"/organizations/{org['id']}/projects", json={"name": "Q3 Analytics"}, headers=_auth(analyst_token)
    )
    assert r.status_code == 403


@requires_postgres
def test_viewer_can_list_but_not_create_projects(client):
    owner_token = _register(client, "owner@example.com")
    org = _create_org(client, owner_token)
    viewer_token = _register(client, "viewer@example.com")
    _add_member(client, owner_token, org["id"], "viewer@example.com", "viewer")
    client.post(f"/organizations/{org['id']}/projects", json={"name": "Q3 Analytics"}, headers=_auth(owner_token))

    listed = client.get(f"/organizations/{org['id']}/projects", headers=_auth(viewer_token))
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    created = client.post(
        f"/organizations/{org['id']}/projects", json={"name": "Another"}, headers=_auth(viewer_token)
    )
    assert created.status_code == 403


@requires_postgres
def test_get_project_requires_membership(client):
    owner_token = _register(client, "owner@example.com")
    org = _create_org(client, owner_token)
    project = client.post(
        f"/organizations/{org['id']}/projects", json={"name": "Q3 Analytics"}, headers=_auth(owner_token)
    ).json()

    outsider_token = _register(client, "outsider@example.com")
    r = client.get(f"/projects/{project['id']}", headers=_auth(outsider_token))
    assert r.status_code == 403


@requires_postgres
def test_member_of_different_org_cannot_see_project(client):
    owner_token = _register(client, "owner@example.com")
    org = _create_org(client, owner_token)
    project = client.post(
        f"/organizations/{org['id']}/projects", json={"name": "Q3 Analytics"}, headers=_auth(owner_token)
    ).json()

    other_owner_token = _register(client, "other-owner@example.com")
    _create_org(client, other_owner_token, name="OtherCo")

    r = client.get(f"/projects/{project['id']}", headers=_auth(other_owner_token))
    assert r.status_code == 403


@requires_postgres
def test_analyst_cannot_delete_project_but_admin_can(client):
    owner_token = _register(client, "owner@example.com")
    org = _create_org(client, owner_token)
    project = client.post(
        f"/organizations/{org['id']}/projects", json={"name": "Q3 Analytics"}, headers=_auth(owner_token)
    ).json()

    analyst_token = _register(client, "analyst@example.com")
    _add_member(client, owner_token, org["id"], "analyst@example.com", "analyst")

    denied = client.delete(f"/projects/{project['id']}", headers=_auth(analyst_token))
    assert denied.status_code == 403

    allowed = client.delete(f"/projects/{project['id']}", headers=_auth(owner_token))
    assert allowed.status_code == 204


@requires_postgres
def test_unauthenticated_request_rejected(client):
    r = client.get("/organizations")
    assert r.status_code == 401
