from tests.routers.conftest import requires_postgres

EMAIL = "bob@example.com"
PASSWORD = "correct-horse-battery-staple"


def _register(client, email=EMAIL, password=PASSWORD):
    return client.post("/auth/register", json={"email": email, "password": password})


@requires_postgres
def test_register_returns_token_pair(client):
    r = _register(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


@requires_postgres
def test_register_duplicate_email_rejected(client):
    _register(client)
    r = _register(client)
    assert r.status_code == 409


@requires_postgres
def test_login_with_correct_credentials(client):
    _register(client)
    r = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]


@requires_postgres
def test_login_with_wrong_password_rejected(client):
    _register(client)
    r = client.post("/auth/login", json={"email": EMAIL, "password": "wrong-password"})
    assert r.status_code == 401


@requires_postgres
def test_login_with_unknown_email_rejected(client):
    r = client.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert r.status_code == 401


@requires_postgres
def test_refresh_issues_new_refresh_token(client):
    # Not asserting the access token also changed: it's a stateless JWT signed over
    # (sub, email, iat, exp) with second-level timestamp granularity, so a register+refresh
    # happening within the same second legitimately produces a byte-identical token -- harmless,
    # since access tokens are never tracked for uniqueness/revocation (only refresh tokens are).
    tokens = _register(client).json()
    r = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200, r.text
    new_tokens = r.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]
    assert new_tokens["access_token"]


@requires_postgres
def test_refresh_token_is_single_use(client):
    tokens = _register(client).json()
    first = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert first.status_code == 200

    replay = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert replay.status_code == 401


@requires_postgres
def test_refresh_with_garbage_token_rejected(client):
    r = client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})
    assert r.status_code == 401


@requires_postgres
def test_logout_revokes_refresh_token(client):
    tokens = _register(client).json()
    r = client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 204

    r = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


@requires_postgres
def test_logout_is_idempotent(client):
    tokens = _register(client).json()
    assert client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 204
    assert client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 204
