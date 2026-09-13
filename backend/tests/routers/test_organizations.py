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


@requires_postgres
def test_create_organization_makes_creator_owner(client):
    token = _register(client, "owner@example.com")
    org = _create_org(client, token)
    assert org["my_role"] == "owner"
    assert org["slug"] == "acme"


@requires_postgres
def test_duplicate_org_name_gets_unique_slug(client):
    token = _register(client, "owner@example.com")
    org1 = _create_org(client, token, name="Acme")
    org2 = _create_org(client, token, name="Acme")
    assert org1["slug"] != org2["slug"]


@requires_postgres
def test_list_my_organizations(client):
    token = _register(client, "owner@example.com")
    _create_org(client, token, name="Acme")
    r = client.get("/organizations", headers=_auth(token))
    assert r.status_code == 200
    assert len(r.json()) == 1


@requires_postgres
def test_non_member_cannot_see_members(client):
    owner_token = _register(client, "owner@example.com")
    org = _create_org(client, owner_token)
    outsider_token = _register(client, "outsider@example.com")

    r = client.get(f"/organizations/{org['id']}/members", headers=_auth(outsider_token))
    assert r.status_code == 403


@requires_postgres
def test_owner_can_add_member(client):
    owner_token = _register(client, "owner@example.com")
    org = _create_org(client, owner_token)
    _register(client, "viewer@example.com")

    r = client.post(
        f"/organizations/{org['id']}/members",
        json={"email": "viewer@example.com", "role": "viewer"},
        headers=_auth(owner_token),
    )
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "viewer"


@requires_postgres
def test_adding_unknown_email_returns_404(client):
    owner_token = _register(client, "owner@example.com")
    org = _create_org(client, owner_token)

    r = client.post(
        f"/organizations/{org['id']}/members",
        json={"email": "ghost@example.com", "role": "viewer"},
        headers=_auth(owner_token),
    )
    assert r.status_code == 404


@requires_postgres
def test_analyst_cannot_add_member(client):
    owner_token = _register(client, "owner@example.com")
    org = _create_org(client, owner_token)
    analyst_token = _register(client, "analyst@example.com")
    client.post(
        f"/organizations/{org['id']}/members",
        json={"email": "analyst@example.com", "role": "analyst"},
        headers=_auth(owner_token),
    )
    _register(client, "someone@example.com")

    r = client.post(
        f"/organizations/{org['id']}/members",
        json={"email": "someone@example.com", "role": "viewer"},
        headers=_auth(analyst_token),
    )
    assert r.status_code == 403


@requires_postgres
def test_admin_cannot_grant_owner_role(client):
    owner_token = _register(client, "owner@example.com")
    org = _create_org(client, owner_token)
    admin_token = _register(client, "admin@example.com")
    client.post(
        f"/organizations/{org['id']}/members",
        json={"email": "admin@example.com", "role": "admin"},
        headers=_auth(owner_token),
    )
    _register(client, "someone@example.com")

    r = client.post(
        f"/organizations/{org['id']}/members",
        json={"email": "someone@example.com", "role": "owner"},
        headers=_auth(admin_token),
    )
    assert r.status_code == 403


@requires_postgres
def test_cannot_demote_last_owner(client):
    owner_token = _register(client, "owner@example.com")
    org = _create_org(client, owner_token)

    # Look up our own user_id via the members list (creator is the sole member so far).
    members = client.get(f"/organizations/{org['id']}/members", headers=_auth(owner_token)).json()
    my_user_id = members[0]["user_id"]

    r = client.patch(
        f"/organizations/{org['id']}/members/{my_user_id}",
        json={"role": "admin"},
        headers=_auth(owner_token),
    )
    assert r.status_code == 409


@requires_postgres
def test_can_demote_owner_when_a_second_owner_exists(client):
    owner_token = _register(client, "owner@example.com")
    org = _create_org(client, owner_token)
    _register(client, "second@example.com")
    client.post(
        f"/organizations/{org['id']}/members",
        json={"email": "second@example.com", "role": "owner"},
        headers=_auth(owner_token),
    )

    members = client.get(f"/organizations/{org['id']}/members", headers=_auth(owner_token)).json()
    my_user_id = next(m["user_id"] for m in members if m["email"] == "owner@example.com")

    r = client.patch(
        f"/organizations/{org['id']}/members/{my_user_id}",
        json={"role": "admin"},
        headers=_auth(owner_token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin"


@requires_postgres
def test_admin_cannot_remove_owner(client):
    owner_token = _register(client, "owner@example.com")
    org = _create_org(client, owner_token)
    admin_token = _register(client, "admin@example.com")
    client.post(
        f"/organizations/{org['id']}/members",
        json={"email": "admin@example.com", "role": "admin"},
        headers=_auth(owner_token),
    )
    members = client.get(f"/organizations/{org['id']}/members", headers=_auth(owner_token)).json()
    owner_user_id = next(m["user_id"] for m in members if m["email"] == "owner@example.com")

    r = client.delete(f"/organizations/{org['id']}/members/{owner_user_id}", headers=_auth(admin_token))
    assert r.status_code == 403
