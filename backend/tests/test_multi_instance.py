"""Phase 7 M4: a real multi-instance regression pass. Every previous milestone's test suite
already runs many independently-constructed `create_app()` instances (one per test), but always
one at a time -- this file is the one place that builds two "backend instances" side by side and
proves state that's supposed to be shared (auth/Postgres, the rate-limit cap, the result cache,
job/dataset visibility) really is global, not secretly working because it happens to be the same
process. See backend/docs/phase7_scope.md's M4 success metrics.

Two independently-built `TestClient(create_app())`s stand in for two backend processes behind a
load balancer: each gets its own `PipelineCache`, its own Redis client connection, its own
in-process state, but shares one real Postgres/Redis/MinIO -- the same simulation
`test_rate_limiting.py`'s own cross-instance test already uses for the limiter alone, generalized
here to the whole app.
"""
import pytest
from fastapi.testclient import TestClient

from insightflow_backend.config import settings
from insightflow_backend.main import create_app
from insightflow_backend.pipeline_cache import PipelineCache
from tests.conftest import SAMPLE_DATA_DIR
from tests.db.conftest import db_engine  # noqa: F401 -- ensures tables exist for this module
from tests.infra import requires_infra, reset_rate_limits, run_pending_jobs, skip_if_redis_unavailable
from tests.test_end_to_end_smoke import _auth, _create_project, _register_and_login, requires_groq


def _two_instances():
    return TestClient(create_app()), TestClient(create_app())


@pytest.fixture
def two_instances(db_engine):  # noqa: F811
    skip_if_redis_unavailable()
    reset_rate_limits()
    yield _two_instances


def test_registering_on_one_instance_logs_in_on_another(two_instances):
    a, b = two_instances()
    email = "cross-instance@example.com"
    password = "correct-horse-battery-staple"

    r = a.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text

    # Instance b never saw that register call -- only real Postgres did.
    r = b.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text


def test_auth_rate_limit_is_a_real_global_cap_across_instances(two_instances, monkeypatch):
    # RedisRateLimiter bakes max_requests/window into itself at construction time
    # (main.py's create_app() calls build_auth_rate_limiter() once) -- settings must be
    # monkeypatched BEFORE building the instances, not after.
    monkeypatch.setattr(settings, "auth_rate_limit_max_requests", 2)
    monkeypatch.setattr(settings, "auth_rate_limit_window_seconds", 60.0)
    a, b = two_instances()

    r1 = a.post("/auth/register", json={"email": "u1@example.com", "password": "correct-horse-battery-staple"})
    r2 = b.post("/auth/register", json={"email": "u2@example.com", "password": "correct-horse-battery-staple"})
    assert r1.status_code == r2.status_code == 201, (r1.text, r2.text)

    # A third instance, third request: the cap is 2 total, not 2-per-instance.
    c = TestClient(create_app())
    r3 = c.post("/auth/register", json={"email": "u3@example.com", "password": "correct-horse-battery-staple"})
    assert r3.status_code == 429, r3.text


def _count_calls(cls, method_name, monkeypatch):
    original = getattr(cls, method_name)
    calls = {"n": 0}

    def wrapper(self, *args, **kwargs):
        calls["n"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(cls, method_name, wrapper)
    return calls


@requires_infra
@requires_groq
def test_result_cache_and_job_state_shared_across_instances(two_instances, monkeypatch):
    """The strongest version of the M2/M3 claims: instance B has never touched this dataset --
    its own PipelineCache is stone cold -- yet it (1) can poll a job submitted through instance A
    and completed by a separate worker process, and (2) serves the correct cached analytics
    result without ever building its own engine, proving the result cache is what answered it,
    not a warm per-process cache that happened to already have the data.
    """
    a, b = two_instances()

    token = _register_and_login(a, email="multi-instance@example.com")
    project = _create_project(a, token, org_name="MultiInstanceCo", project_name="Multi Instance Project")

    files = [
        ("files", ("customers.csv", open(SAMPLE_DATA_DIR / "customers.csv", "rb"), "text/csv")),
        ("files", ("orders.csv", open(SAMPLE_DATA_DIR / "orders.csv", "rb"), "text/csv")),
        ("files", ("products.csv", open(SAMPLE_DATA_DIR / "products.csv", "rb"), "text/csv")),
    ]
    submitted = a.post(f"/projects/{project['id']}/schema/discover", files=files, headers=_auth(token))
    assert submitted.status_code == 202, submitted.text
    run_pending_jobs()  # a separate worker process in production; a real RQ SimpleWorker here

    job_id = submitted.json()["id"]
    status_via_b = b.get(f"/projects/{project['id']}/schema/jobs/{job_id}", headers=_auth(token))
    assert status_via_b.status_code == 200, status_via_b.text
    assert status_via_b.json()["status"] == "done", status_via_b.json()

    engine_build_calls = _count_calls(PipelineCache, "get_or_build_engine", monkeypatch)
    query = {"operation": "aggregate", "metric": "revenue"}

    first = a.post(f"/projects/{project['id']}/analytics/query", json=query, headers=_auth(token))
    assert first.status_code == 200, first.text
    assert engine_build_calls["n"] == 1  # instance A's own cold PipelineCache, built once

    second = b.post(f"/projects/{project['id']}/analytics/query", json=query, headers=_auth(token))
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    # Instance b's PipelineCache never gets touched -- the shared Redis result cache answers it.
    assert engine_build_calls["n"] == 1
