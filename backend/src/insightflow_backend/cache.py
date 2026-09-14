"""Redis-backed result cache (Phase 7 M2) -- caches the JSON-serializable *outputs* of
analytics/query, dashboard/generate, and chat/ask, keyed by (dataset_id, route, canonicalized
request body). See backend/docs/phase7_scope.md's "explicitly reconsidered" section for why this
replaces the earlier idea of sharing `PipelineCache` itself via Redis: a DuckDB connection and a
pipeline object can't cross a process boundary through Redis, but a `MetricResult`/
`HydratedDashboard`/`Answer` can.

A `Dataset` row is immutable once created (a re-upload or a mapping review makes a new row --
db/models.py), so the *data* behind a key never changes. But the *rules for computing on it* can:
Phase 8 M4 changed which mappings the engine may use and how measures bind to entities, and
afterwards this cache kept serving an AOV of 22.82 computed under the old rules for a dataset whose
correct answer is 160.99. So a cached result is correct only for the dataset AND the computation
rules that produced it -- `COMPUTATION_VERSION` below is the second half of that, and the TTL only
bounds Redis memory.
"""
import hashlib
import json

import redis

# Bump whenever a change alters the number, dashboard or answer produced for an unchanged dataset
# (binding rules, which mappings are trusted, compiler semantics, a registry definition). Old entries
# are then never read again and simply expire -- no flush needed on deploy.
#   2 -- Phase 8 M4: per-dataset measure binding; engine computes only on trusted mappings.
#   3 -- Phase 8: time filters through a related table, result caveats, categorical-only planner
#        dimensions, dashboard growth signals from the data's own date.
#   4 -- Phase 8: caveated metrics withheld from the dashboard planner; caveated chat answers
#        explained deterministically.
#   5 -- Phase 8: relative periods anchored on the last month of real volume, not a sparse tail.
#   6 -- Phase 8 M4: results carry the registry's display format; LLM text never adds a currency.
#   7 -- Phase 8 M4: pie charts fetch every group instead of the top 20.
#   8 -- Phase 8 M5: chat answers carry the queries run and the date periods are measured from.
#   9 -- Phase 8 M5: grouped results ordered largest-first by default and flagged when truncated.
#  10 -- Phase 8 M5: chat explanations receive formatted values and a bounded row sample.
#  11 -- Phase 8 M6: growth over last month/quarter/year compares with the preceding calendar period.
#  12 -- Phase 8 M6: value-limited questions refused; explanations with ungrounded numbers withheld.
#  13 -- Phase 8 M6: dashboard narratives/rationales with ungrounded numbers withheld; refusals name dimensions.
COMPUTATION_VERSION = 13


def build_cache_key(dataset_id, route: str, payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(f"v{COMPUTATION_VERSION}:{dataset_id}:{route}:{canonical}".encode()).hexdigest()


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
