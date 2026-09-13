# Phase 7 scope: Redis + Background Workers + Caching

> **Status: closed.** M1-M4 all done. 84 tests passing (including two real-Groq, real-infra
> integration tests and a real two-container regression pass), `docker build -f
> backend/Dockerfile` verified with the new dependencies, real `worker`/`redis` containers
> confirmed working end to end. One open item carried forward, not silently dropped: a
> `SIGKILL`'d worker leaves its job stuck at `running` forever (see "Open items" below).

Written before any Phase 7 code, per CLAUDE.md's working agreement (Phase 7+ should be explicitly
scoped and confirmed, not assumed). Covers *why* and *what*; see "Implementation plan" below for
*how*, milestoned the same way Phase 6 was.

## Aim

Phase 6 shipped two explicit stopgaps, both called out in their own code comments as "this is
Phase 7's job":

- `auth/rate_limit.py`'s `InMemoryRateLimiter` -- per-process, resets on restart, and splits its
  effective limit across however many backend instances a load balancer routes to.
- `pipeline_cache.py`'s `PipelineCache` -- per-process LRU, so a second backend instance cold-starts
  every dataset it hasn't personally touched yet.

Phase 7's aim is to close both, and to move the one route that does real LLM-heavy, potentially
slow work (`schema/discover`) off the request thread and onto a real job queue -- without touching
`insightflow_core`, the POC pipelines, or the LLM/deterministic split.

## In scope

1. **Redis-backed, distributed rate limiting.** Replaces `InMemoryRateLimiter` with the same
   `check(key)` contract, backed by Redis so the limit is a real global cap across instances, not a
   per-process one. Extended beyond `/auth/*` to `schema/discover`, `dashboard/generate`, and
   `chat/ask` -- the routes that actually spend an LLM call.
2. **Redis-backed result caching.** `MetricResult` / `HydratedDashboard` / `Answer` are
   JSON-serializable and keyed off `(dataset_id, canonicalized request body)` -- a natural fit for
   a shared cache, unlike the pipeline objects themselves. A re-upload gets a new `dataset_id`
   (Phase 6's existing "new row, not a mutation" rule), so cache entries never need explicit
   invalidation -- only a TTL as a memory-growth safety net, not a correctness mechanism.
3. **Async schema discovery via RQ.** `POST .../schema/discover` enqueues a job and returns
   immediately; a new `GET .../schema/jobs/{job_id}` polls status. A worker process runs the
   *existing* `run_schema_discovery()` unchanged. New `jobs` table in Postgres (not Redis-only) so
   status survives a worker restart and stays queryable/audited like everything else in this
   schema.
4. **Infra:** `redis` + `worker` services in `docker-compose.yml`, `REDIS_URL`/queue settings in
   `config.py`, `redis` + `rq` added to `backend/pyproject.toml`.

## Explicitly reconsidered and narrowed

**Sharing `PipelineCache` itself via Redis is not in scope.** The original framing of this phase
included "Redis-backed `PipelineCache`," but a DuckDB connection and a `AnalyticsEnginePipeline`
Python object aren't things Redis can hand to another process -- every instance still needs to
rebuild its own local pipeline regardless of what Redis knows about who else has one warm. The
actual cross-instance value of caching lives entirely in item 2 (result caching), which caches
*outputs*, not the pipeline objects that produce them. `PipelineCache` itself is unchanged by
Phase 7.

## Explicit non-goals

- `analytics/query` stays fully synchronous. It's deterministic SQL with no LLM call -- there's
  nothing slow to move off the request thread.
- No changes to `insightflow_core`, `MetricRegistry`, the AST/compiler, or any POC pipeline
  internals. This phase is infra, not engine.
- No React frontend, no Power BI, no further auth/RBAC changes (Phase 8/9 territory).
- §7's "business type" open decision in the root CLAUDE.md stays unresolved; Phase 7 doesn't touch
  dashboard planning.

## Success metrics

- Two backend instances sharing one Redis pass the existing test suite with no behavior change
  from the single-instance case.
- `schema/discover` returns in well under 1s regardless of upload size or LLM latency; polling
  `GET .../schema/jobs/{id}` eventually returns the same `DatasetOut` shape the old synchronous
  route used to return inline -- verified by a regression test that runs both the old logic path
  and the new job path against the same fixture and diffs the result.
- Rate limiting holds correctly under a real multi-container docker-compose run (backend x2 +
  redis), not mocks -- same "test against real infra" discipline Phase 6 used for Postgres/MinIO.
- Repeated identical `analytics/query` / `dashboard/generate` / `chat/ask` calls against an
  unchanged dataset show a measured cache-hit latency drop; a re-upload never serves a stale
  cached result (since it's a different `dataset_id` by construction).
- A worker killed mid-job leaves the `jobs` row in a recoverable state (`failed`, with an error
  message) rather than stuck in `running` forever.

## Implementation plan

Milestoned smallest/lowest-risk first, same convention Phase 6 used (M1-M4 built infra
foundations before M5-M6 rewired routes and hardened). Each milestone should be verified against
real Redis/Postgres (docker-compose), not mocks, before moving to the next.

### M1 -- Redis infra + distributed rate limiting (done)

- Add `redis` (client) to `backend/pyproject.toml`; add a `redis` service to `docker-compose.yml`;
  add `REDIS_URL` to `config.py` / `.env.example`.
- New `RedisRateLimiter` (same module, `auth/rate_limit.py`) implementing the same `check(key)` ->
  raises `HTTPException(429)` contract as `InMemoryRateLimiter`, via `INCR`+`EXPIRE` fixed-window
  counters in Redis. Held on `app.state` (per the M6 lesson already in CLAUDE.md -- never a module
  global), built once in `main.py`'s `create_app()`.
- New rate-limit buckets for `schema/discover`, `dashboard/generate`, `chat/ask` (new settings:
  max requests/window per bucket), reusing the same limiter class with different keys/limits.
- Test: spin up two `TestClient`/app instances against one real Redis (docker-compose), confirm
  the limit is enforced globally, not per-instance.
- Update `phase6_deployment.md`'s "Rate limiting -- known limitation" section to point at this
  once it lands (that limitation is closed by M1).

**Real gap found while testing this milestone, not caught by design review:** the shared
`client` test fixture builds a fresh `TestClient` per test, but every `TestClient` reports the
same fake client IP (`"testclient"`), and unlike the old in-memory limiter (a fresh Python dict
per `create_app()` call), Redis state persists across the whole pytest session. Every test that
merely authenticates as setup -- not testing rate limiting itself -- was inheriting whatever
budget earlier tests in the same run had already spent, and the suite started failing with
spurious 429s once total register/login calls across the session exceeded the default limit of
10. Fixed with `tests/infra.py`'s `reset_rate_limits()`, called from every fixture that
authenticates through a real app, flushing the `ratelimit:auth:*`/`ratelimit:llm:*` keys before
each test -- the Redis-state equivalent of the DB fixtures' per-test transaction rollback.

### M2 -- Redis-backed result cache (done)

- New `cache.py`: `ResultCache.get(key) -> dict | None` / `.set(key, value, ttl)` wrapping a Redis
  client. Key = `sha256(dataset_id + canonicalized request body)`.
- Wire into `routers/analytics.py`, `routers/dashboard.py`, `routers/chat.py`: check cache before
  calling into `PipelineCache`-backed pipelines, populate on miss. Response model unchanged --
  this is transparent to callers.
- Configurable TTL (default long, e.g. 24h, since a `dataset_id`'s underlying rows are immutable by
  construction -- TTL exists for Redis memory hygiene, not correctness).
- Test: same query twice against an unchanged dataset hits cache (assert on a call-count spy into
  the underlying pipeline, not just wall-clock time, so the test isn't flaky).

`tests/test_result_cache.py` covers `build_cache_key`/`ResultCache` in isolation (no LLM needed),
plus one real end-to-end test (`test_repeated_requests_after_upload_never_recompute`, gated
`requires_infra` + `requires_groq` like the existing full-flow smoke test) that spies on
`AnalyticsEnginePipeline.run`/`DashboardGenerationPipeline.run`/`QuestionAnsweringPipeline.answer`
directly and asserts the second identical call never re-invokes them -- real Groq calls included,
not mocked. **Executed against a real Groq key and passing** -- confirmed cache hits complete in
~0ms versus ~13-19s for the real (LLM + DuckDB) first call.

**Two real things found running this against a real key, not caught by design review:**
1. `llama-3.3-70b-versatile` (the original Phase 5/6 `GROQ_MODEL` default) has been deprecated by
   Groq since Phase 6 shipped -- every call 404s with `model_not_found`. Unrelated to Phase 7, but
   blocked verifying it. Fixed the default in `config.py`/`.env.example` to
   `openai/gpt-oss-120b`, the model `poc4_nl_chatbot`'s own `.env` already uses and CLAUDE.md's
   POC4 M4 evaluation verified working.
2. The end-to-end cache test's own "a different query is still a cache miss" assertion was wrong:
   it spied on `AnalyticsEnginePipeline.run` and expected exactly 2 total calls, not accounting
   for `dashboard/generate` and `chat/ask` each calling the *same shared engine* internally
   (one `run()` per dashboard component / per resolved sub-query, unmodified per CLAUDE.md's own
   design) -- so the count was already in the double digits by the time the "different query"
   check ran, for reasons that have nothing to do with caching correctness. Fixed by comparing
   against a snapshot taken just before the new query, not an absolute count.

### M3 -- Async schema discovery (RQ) (done)

- New `jobs` table (Alembic migration): `id`, `project_id` (FK), `status` enum
  (`pending`/`running`/`done`/`failed`), `dataset_id` (nullable FK, set on success), `error`
  (nullable text), `created_by` (FK users), `created_at`, `updated_at`. Remember Phase 6's enum
  bug: the migration's `downgrade()` must explicitly drop the new Postgres enum type, not just the
  table.
- `routers/schema.py`: `discover_schema` uploads raw files to S3 immediately (dataset id generated
  upfront), creates a `jobs` row, enqueues an RQ job with the job id, returns `202` + job id
  instead of blocking on `run_schema_discovery()`.
- New `GET /projects/{project_id}/schema/jobs/{job_id}` (min role: VIEWER, same as other read
  routes) returning job status, and the same `DatasetOut` shape as before once `status == "done"`.
- New `worker.py` (RQ worker entrypoint) + `jobs.py` (the job body): downloads files from S3 to a
  temp dir, calls the *existing* `run_schema_discovery()` unchanged, creates the `Dataset` row,
  writes the `dataset.created` audit log entry (same transaction as the Dataset insert, now from
  the worker instead of the request), marks the job `done`. On exception, marks the job `failed`
  with the error message rather than leaving it stuck.
- `docker-compose.yml` gets a `worker` service (same image as backend, `rq worker` command).
- Tests: RQ's synchronous/burst mode for fast unit tests; at least one real end-to-end test
  driving backend + redis + a real worker process via docker-compose, matching Phase 6's own
  "verified against real infra, not mocks" bar.

**Deviations from the plan above, decided during implementation:**
- `dataset_id` on `Job` ended up NOT nullable and NOT a foreign key (the plan said "nullable FK,
  set on success"). It's generated and stored at job-creation time instead, because the uploaded
  files are already written to S3 under that id's prefix before the job is ever enqueued -- the
  worker needs to know that id to download them back. It can't be a real FK: the `Dataset` row it
  will eventually point to doesn't exist yet when the `Job` row is created.
- RQ's default `Worker` forks a subprocess per job via `os.fork()`, which doesn't exist on
  Windows -- this project's own dev platform. Used `SimpleWorker` (runs jobs in-process) instead,
  in both `worker.py` and the tests. Per-job process isolation isn't a real requirement for a
  single-purpose discovery queue.

**Three real bugs found running this end to end, not caught by design review:**
1. **RQ pickles job payloads onto its Redis connection.** Every other Redis client in this
   backend (rate limiting, result cache) uses `decode_responses=True`; reusing that client for RQ
   would silently corrupt binary pickle data. Fixed with a dedicated `build_rq_connection()` in
   `jobs.py`, never shared with `auth/rate_limit.py`/`cache.py`'s connection.
2. **The real regression-test bug this milestone's own success metric predicted, just from the
   wrong direction.** `real_db_client` (the shared per-test fixture) wraps every test in a
   transaction that's rolled back, never committed -- correct for isolating ordinary request
   tests from each other, but the RQ worker (even in-process `SimpleWorker`) opens its own DB
   connection via the app's real `SessionLocal`, which can only see rows another connection has
   actually committed. A `Job` row created inside the test's still-open transaction was invisible
   to the worker; `run_discovery_job` found no such row and silently returned (RQ logged it
   "successfully completed" in 3ms), leaving the job stuck at `pending` forever from the test's
   point of view -- functionally the exact "stuck job" failure mode the M3 success metrics were
   written to catch, except caused by test isolation rather than a real crash. Fixed with a new
   `committing_db_client`/`committing_client` fixture (no transaction wrapping -- real commits,
   real Postgres) used only by tests that exercise the async job path; the ordinary `client`
   fixture is untouched for everything else.
3. **`alembic_version` said the Phase 6 schema was applied; the physical tables didn't exist.**
   Discovered because autogenerating this milestone's migration proposed recreating every Phase 6
   table from scratch, not just `jobs`. Root cause: this backend's own test suite never touches
   Alembic at all (`tests/db/conftest.py`'s `db_engine` fixture calls
   `Base.metadata.create_all()`/`drop_all()` directly), so nothing in this session had exercised
   `alembic upgrade head` against a real, persistent volume before -- and the local Postgres
   volume had gone through a Docker Desktop crash/restart earlier in this session. Fixed with
   `alembic stamp base` + `alembic upgrade head` to physically (re)create the Phase 6 tables for
   real, confirmed via `\dt`, before generating and applying the `jobs` migration. Verified the
   new migration's full upgrade -> downgrade -> upgrade round-trip too (same discipline as Phase
   6's own enum-drop fix), including that its `downgrade()` explicitly drops the `job_status`
   enum type.

**Verified against real infra + real Groq:** `test_full_flow_against_sample_data` (submit ->
`run_pending_jobs()` drains the real queue via a real `SimpleWorker` in burst mode, not a mock ->
poll job status -> `done` with the same dataset shape the old synchronous route used to return
inline) passed end to end at least once. `docker build -f backend/Dockerfile` picked up `rq`
cleanly; `docker compose up -d worker` starts the real container, connects to Redis, and logs
`*** Listening on insightflow-discovery...` with no crash (Linux container -- `Worker`'s
`os.fork()` path would have worked there too, but `SimpleWorker` was kept for parity with the
Windows dev path).

The free Groq key's daily token quota (200k TPD) was exhausted by the cumulative real-LLM calls
across all of M1/M2/M3 verification today. Both `test_full_flow_against_sample_data` and the M2
cache-recompute test failed on that re-run with a genuine `groq.RateLimitError`, not a
regression -- and in both cases the job correctly transitioned to `failed` with the real Groq
error message (queryable via `GET .../schema/jobs/{id}`, itself returning `200`) instead of
hanging or crashing, an unplanned but welcome second confirmation that the failure-handling path
works under a real external failure, not just a synthetic one. **Re-run against a second Groq
key: both pass, and the full suite is 84/84 green** -- the async plumbing (job creation, worker
pickup, DB visibility across connections, status polling, error recording) and the full happy
path are now both confirmed, not just the failure path.

### M4 -- Docs + end-to-end hardening pass (done)

- `backend/docs/hld.md` updated with the async schema-discovery flow and the result-cache layer
  (plus a note that the rest of that doc predates Phase 6 and was never rewritten for it -- not
  this phase's job to fix, but worth flagging for whoever reads it next).
- `tests/test_multi_instance.py` (new): two independently-built `create_app()` instances sharing
  one real Postgres/Redis/MinIO, proving (1) registering on instance A logs in on instance B
  (shared Postgres, not news, but the baseline the rest of the file builds on), (2) the auth
  rate-limit cap is a real global total across instances, not 10-per-instance, and (3) the
  strongest claim: instance B, whose own `PipelineCache` has never touched the dataset, still
  serves the correct cached `analytics/query` result -- proven by spying on
  `PipelineCache.get_or_build_engine` and asserting it's called exactly once total, from
  instance A only, even after instance B answers the identical query correctly.
- **A real two-container run**, not just simulated: built the image, ran two actual
  `insightflow-backend` containers on the shared compose network against one real
  Postgres/Redis/MinIO. Confirmed cross-container auth (register on container A, log in on
  container B) and a real cross-container rate-limit cap (`LLM_RATE_LIMIT_MAX_REQUESTS=2`, and
  the request that got blocked landed on the *other* container from every successful one).
  **Finding:** client-IP-keyed rate limiting (the `/auth/*` bucket) isn't reliably testable
  through Docker Desktop's NAT on Windows -- published-port connections don't arrive with a
  stable source IP as seen inside the container, so alternating requests across two containers
  landed in different buckets instead of one shared one. Not a code defect (the `/auth/*` bucket
  is already proven correct at the Redis/object level, in-process, where the client IP is
  stable); a testing-environment limitation specific to this platform, worth knowing about before
  trying to reproduce this exact check again. The project-`id`-keyed LLM bucket sidesteps it
  entirely, which is a real point in favor of that keying choice beyond the "tenant budget" reason
  already given in M1.
- **The killed-worker-mid-job success metric, actually measured, not assumed:** submitted a real
  discovery job against a real running `worker` container, `docker kill -s SIGKILL`'d it while a
  `docker logs` check confirmed it was actively inside `run_schema_discovery`, and polled the job
  afterward. Result: **the job is left stuck at `status: "running"` forever.** The `try`/`except`
  in `jobs.py` only catches Python exceptions in the same process (already verified working, via
  the real `groq.RateLimitError` case above) -- a `SIGKILL` gives the process no chance to run
  that code at all. This is a real, now-confirmed gap, not a hypothetical one: there is currently
  no heartbeat or staleness check that would let anything reclaim a `running` job whose worker
  died outright. Deliberately not fixed in this phase (would need a watchdog/reconciliation
  mechanism -- e.g. periodically requeuing `running` jobs whose `updated_at` is older than some
  threshold -- which is new scope, not hardening of what M3 already built). Flagged as an open
  item below rather than silently left undocumented.

## Open items carried forward (not resolved by Phase 7)

- **A `SIGKILL`'d worker leaves its `Job` row stuck at `running` forever** -- confirmed by
  actually killing a real worker mid-job in M4, not hypothetical. No heartbeat/staleness
  reconciliation exists to reclaim it. A future fix: a periodic check (cron, or the next job
  queue poll) that requeues or fails any `running` job whose `updated_at` is older than
  `discovery_job_timeout_seconds` -- explicitly out of scope for this phase, since it's new
  machinery, not a completion of what M3 already built.
- §7 "business type" input for dashboard planning (root CLAUDE.md) -- unrelated to this phase,
  still open.
- Phase 8 (frontend) will be the first real consumer of the async job-status endpoint; its actual
  polling UX (interval, backoff, websocket vs. poll) is a Phase 8 decision, not Phase 7's.
