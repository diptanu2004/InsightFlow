"""Phase 7 M2: Redis-backed result caching. Unit-level checks for `cache.py` itself, plus one
real end-to-end test proving a repeated identical request never re-invokes the underlying
pipeline (real Groq calls included) -- the actual point of this milestone, not just "should work
in theory". See backend/docs/phase7_scope.md.
"""
import uuid

import pytest

from insightflow_backend.auth.rate_limit import build_redis_client
from insightflow_backend.cache import ResultCache, build_cache_key
from tests.conftest import SAMPLE_DATA_DIR
from tests.infra import requires_infra, requires_redis
from tests.test_end_to_end_smoke import (
    _auth,
    _create_project,
    _register_and_login,
    _upload_sample_files_and_wait,
    requires_groq,
)

pytestmark = requires_redis


def test_cache_key_differs_by_dataset_id():
    a = build_cache_key(uuid.uuid4(), "route", {"x": 1})
    b = build_cache_key(uuid.uuid4(), "route", {"x": 1})
    assert a != b  # a re-upload's new Dataset row must never collide with the old one's cache


def test_cache_key_differs_by_route():
    dataset_id = uuid.uuid4()
    a = build_cache_key(dataset_id, "analytics_query", {"x": 1})
    b = build_cache_key(dataset_id, "chat_ask", {"x": 1})
    assert a != b


def test_cache_key_differs_by_payload():
    dataset_id = uuid.uuid4()
    a = build_cache_key(dataset_id, "route", {"question": "revenue?"})
    b = build_cache_key(dataset_id, "route", {"question": "profit?"})
    assert a != b


def test_cache_key_is_stable_regardless_of_dict_key_order():
    dataset_id = uuid.uuid4()
    a = build_cache_key(dataset_id, "route", {"x": 1, "y": 2})
    b = build_cache_key(dataset_id, "route", {"y": 2, "x": 1})
    assert a == b


@pytest.fixture
def result_cache():
    client = build_redis_client()
    cache = ResultCache(client, ttl_seconds=60)
    yield cache
    for key in client.scan_iter(match="result:*"):
        client.delete(key)


def test_set_then_get_round_trips(result_cache):
    assert result_cache.get("missing-key") is None
    result_cache.set("some-key", '{"value": 42}')
    assert result_cache.get("some-key") == '{"value": 42}'


def test_ttl_is_applied(result_cache):
    result_cache.set("some-key", "value")
    ttl = result_cache._client.ttl("result:some-key")
    assert 0 < ttl <= 60


@requires_infra
@requires_groq
def test_repeated_requests_after_upload_never_recompute(committing_client, monkeypatch):
    """The whole point of M2: a second identical call must be served from Redis without
    re-invoking the pipeline -- proven here with a real call-count spy around the actual
    pipeline classes (real Groq, real DuckDB), not a mock standing in for them.

    Uses `committing_client`, not the ordinary rollback-per-test `client`: schema/discover is
    now an async job (Phase 7 M3), and the RQ worker sees the DB through its own connection --
    it can't see a Job row sitting inside another connection's still-open transaction. See
    tests/infra.py's `committing_db_client` docstring.
    """
    client = committing_client
    from insightflow_core.pipeline import AnalyticsEnginePipeline
    from insightflow_chatbot.pipeline import QuestionAnsweringPipeline
    from insightflow_dashboard.dashboard.pipeline import DashboardGenerationPipeline

    def _count_calls(cls, method_name):
        original = getattr(cls, method_name)
        calls = {"n": 0}

        def wrapper(self, *args, **kwargs):
            calls["n"] += 1
            return original(self, *args, **kwargs)

        monkeypatch.setattr(cls, method_name, wrapper)
        return calls

    engine_calls = _count_calls(AnalyticsEnginePipeline, "run")
    dashboard_calls = _count_calls(DashboardGenerationPipeline, "run")
    chat_calls = _count_calls(QuestionAnsweringPipeline, "answer")

    token = _register_and_login(client, email="cache-smoke@example.com")
    project = _create_project(client, token, org_name="CacheCo", project_name="Cache Project")
    r = _upload_sample_files_and_wait(client, token, project["id"])
    assert r.status_code == 200 and r.json()["status"] == "done", r.text

    query = {"operation": "aggregate", "metric": "revenue"}

    r1 = client.post(f"/projects/{project['id']}/analytics/query", json=query, headers=_auth(token))
    r2 = client.post(f"/projects/{project['id']}/analytics/query", json=query, headers=_auth(token))
    assert r1.status_code == r2.status_code == 200, (r1.text, r2.text)
    assert r1.json() == r2.json()
    assert engine_calls["n"] == 1

    d1 = client.post(f"/projects/{project['id']}/dashboard/generate", headers=_auth(token))
    d2 = client.post(f"/projects/{project['id']}/dashboard/generate", headers=_auth(token))
    assert d1.status_code == d2.status_code == 200, (d1.text, d2.text)
    assert d1.json() == d2.json()
    assert dashboard_calls["n"] == 1

    question = {"question": "What was total revenue?"}
    c1 = client.post(f"/projects/{project['id']}/chat/ask", json=question, headers=_auth(token))
    c2 = client.post(f"/projects/{project['id']}/chat/ask", json=question, headers=_auth(token))
    assert c1.status_code == c2.status_code == 200, (c1.text, c2.text)
    assert c1.json() == c2.json()
    assert chat_calls["n"] == 1

    # A genuinely different query must still be a cache miss, not just always-cached. Compare
    # against a snapshot taken here, not an absolute count: dashboard/generate and chat/ask each
    # call the shared AnalyticsEnginePipeline internally too (one `run()` per dashboard
    # component / per resolved sub-query, unmodified per CLAUDE.md's design), so engine_calls
    # already grew by an unpredictable amount from the d1/d2/c1/c2 calls above -- none of that
    # is what this assertion is about.
    engine_calls_before_new_query = engine_calls["n"]
    other_query = {"operation": "aggregate", "metric": "orders"}
    r3 = client.post(f"/projects/{project['id']}/analytics/query", json=other_query, headers=_auth(token))
    assert r3.status_code == 200, r3.text
    assert engine_calls["n"] > engine_calls_before_new_query


def test_changing_the_computation_version_changes_every_key(monkeypatch):
    """Phase 8 M4: Dataset immutability covers the data, not the rules for computing on it. After a
    change to measure binding, this cache kept serving an AOV of 22.82 for a dataset whose correct
    answer is 160.99 -- same dataset_id, same request, same key. The version is what makes the key
    differ once the rules do."""
    import insightflow_backend.cache as cache_module

    dataset_id = uuid.uuid4()
    before = build_cache_key(dataset_id, "analytics_query", {"metric": "aov"})
    monkeypatch.setattr(cache_module, "COMPUTATION_VERSION", cache_module.COMPUTATION_VERSION + 1)
    after = cache_module.build_cache_key(dataset_id, "analytics_query", {"metric": "aov"})
    assert before != after
