"""Redis-backed result cache (Phase 7 M2) -- caches the JSON-serializable *outputs* of
analytics/query, dashboard/generate, and chat/ask, keyed by (dataset_id, route, canonicalized
request body). See backend/docs/phase7_scope.md's "explicitly reconsidered" section for why this
replaces the earlier idea of sharing `PipelineCache` itself via Redis: a DuckDB connection and a
pipeline object can't cross a process boundary through Redis, but a `MetricResult`/
`HydratedDashboard`/`Answer` can.

A `Dataset` row is immutable once created (a re-upload makes a new row -- db/models.py), so a
cache hit is correct for as long as it lives; the TTL below exists only to bound Redis memory
growth, not for correctness -- nothing here ever needs explicit invalidation.
"""
import hashlib
import json

import redis


def build_cache_key(dataset_id, route: str, payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(f"{dataset_id}:{route}:{canonical}".encode()).hexdigest()


class ResultCache:
    def __init__(self, client: redis.Redis, ttl_seconds: int) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds

    def get(self, key: str) -> str | None:
        return self._client.get(f"result:{key}")

    def set(self, key: str, value: str) -> None:
        self._client.set(f"result:{key}", value, ex=self._ttl_seconds)


def build_result_cache(client: redis.Redis, ttl_seconds: int) -> ResultCache:
    return ResultCache(client, ttl_seconds=ttl_seconds)
