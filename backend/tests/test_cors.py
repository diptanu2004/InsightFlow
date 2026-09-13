"""CORS defaults to deny-all (no frontend exists yet -- Phase 8) and only allows origins an
operator explicitly lists via CORS_ALLOWED_ORIGINS. No Postgres/MinIO needed: /health doesn't
touch either.
"""
from fastapi.testclient import TestClient

from insightflow_backend.config import settings


def _make_client(monkeypatch, allowed_origins):
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-for-cors-checks")
    monkeypatch.setattr(settings, "cors_allowed_origins", allowed_origins)
    from insightflow_backend.main import create_app

    return TestClient(create_app())


def test_no_allowed_origins_by_default_denies_cross_origin(monkeypatch):
    client = _make_client(monkeypatch, [])
    r = client.get("/health", headers={"Origin": "http://evil.example.com"})
    assert "access-control-allow-origin" not in r.headers


def test_listed_origin_is_allowed(monkeypatch):
    client = _make_client(monkeypatch, ["http://localhost:3000"])
    r = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_unlisted_origin_is_still_denied_when_others_are_allowed(monkeypatch):
    client = _make_client(monkeypatch, ["http://localhost:3000"])
    r = client.get("/health", headers={"Origin": "http://evil.example.com"})
    assert "access-control-allow-origin" not in r.headers


def test_response_carries_a_request_id_header(monkeypatch):
    client = _make_client(monkeypatch, [])
    r = client.get("/health")
    assert r.headers.get("x-request-id")
