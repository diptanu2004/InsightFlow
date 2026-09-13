# Phase 5 Backend — High-Level Design

> **Status:** Closed. All 4 package renames landed and every POC's own test suite (plus
> `insightflow_core`'s) re-verified green standalone; this package's own test suite, including a
> real end-to-end run (`/schema/discover` -> `/analytics/query` -> `/dashboard/generate` ->
> `/chat/ask`) against real sample data and real Groq calls, passes. `docker build -f
> backend/Dockerfile -t insightflow-backend .` from the repo root builds cleanly and the resulting
> container serves `/health` (200) and `/docs` (200) correctly.

## Scope

One FastAPI process wiring POC 1's schema discovery, `insightflow_core`'s analytics engine,
POC 3's dashboard generation, and POC 4's NL chatbot behind four HTTP endpoints plus a health
check. This is integration, not new capability — every pipeline class and Pydantic model is
reused unchanged from its own POC; the backend adds only HTTP routing, in-memory session state,
and one canonical `bootstrap_registry()`.

```
POST /schema/discover  -> SchemaDiscoveryPipeline.run()          -> SemanticModel (core's)
POST /analytics/query  -> AnalyticsEnginePipeline.run()          -> MetricResult
POST /dashboard/generate -> DashboardGenerationPipeline.run()    -> HydratedDashboard
POST /chat/ask         -> QuestionAnsweringPipeline.answer()     -> Answer
GET  /health
```

## Why a package rename was required

All four POCs declared the same top-level import package (`insightflow`) with independent
venvs — fine in isolation, but importing all four into one process collides. Each POC's `src/`
package and `[project].name` were renamed once (`insightflow_schema_discovery`,
`insightflow_analytics`, `insightflow_dashboard`, `insightflow_chatbot`) with no behavior change;
each POC's own test suite was re-verified green standalone before this package was built on top.

## Session state (deliberately minimal)

Phase 6 (auth + multi-tenancy + Postgres) doesn't exist yet, so this backend holds exactly one
in-memory `SessionState` in `app.state` — the most recently uploaded dataset's `SemanticModel` +
`data_dir`, plus lazily-built and cached `AnalyticsEnginePipeline` /
`DashboardGenerationPipeline` / `QuestionAnsweringPipeline` instances (each involves a real
`ChatGroq` client and/or DuckDB view registration, so they're built once per dataset, not once
per request). A new `/schema/discover` call resets all of it. Calling `/analytics/query`,
`/dashboard/generate`, or `/chat/ask` before any upload returns `409`, not a crash.

`/analytics/query` and `/chat/ask` share one `AnalyticsEnginePipeline` instance per session —
`QuestionAnsweringPipeline` requires an already-built engine as a constructor argument rather
than building its own, so `SessionState.get_or_build_chat_pipeline()` reuses
`get_or_build_engine()`'s result instead of registering the same DuckDB sources twice.

## Known Phase 5 limitations (by design, not oversight)

- Single global session, no per-user/per-project isolation — Phase 6.
- Uploads are `.csv` only. `insightflow_core`'s `QueryExecutor.register_sources` hardcodes a
  `.csv` lookup per entity regardless of what POC 1's `FileParser` would otherwise accept, so
  accepting `.xlsx`/`.xls` here would let schema discovery succeed while every downstream query
  silently failed to find its source file.
- `registry/bootstrap.py` here is a fourth, canonical copy of the same registry vendored in
  POC 2/3/4 — collapsing those three into a dependency on this package would invert the
  dependency direction (each POC would need to depend on the backend) and is out of scope.
- No Redis/background workers (Phase 7), no frontend (Phase 8), no Power BI (Phase 9).

Note: this doc predates Phase 6 (auth + multi-tenancy) and was never rewritten for it -- Phase
6's own design decisions were deliberately captured inline in code docstrings instead of a
second HLD pass (see CLAUDE.md §6). The "single global session" limitation above is Phase 5
history now, superseded by Phase 6's `Dataset`/`PipelineCache` model; read this doc for the
original routing/rename rationale, not as current-state session architecture.

## Phase 7 additions (Redis + background workers + caching)

Full design rationale and the milestone-by-milestone implementation log (including every real
bug found running each piece against real infra) live in `backend/docs/phase7_scope.md` -- this
section is just the resulting shape of the request flow, for whoever reads this HLD next.

```
POST /schema/discover        -> enqueue Job -> 202 + job id      (was: 201 + Dataset, inline)
GET  /schema/jobs/{job_id}   -> poll Job status/result            (new)
POST /analytics/query        -> ResultCache check -> AnalyticsEnginePipeline.run()  (on miss)
POST /dashboard/generate     -> ResultCache check -> DashboardGenerationPipeline.run()  (on miss)
POST /chat/ask               -> ResultCache check -> QuestionAnsweringPipeline.answer()  (on miss)
```

- **Schema discovery is async now.** The LLM-heavy relationship-reasoning work moved off the
  request thread onto an RQ job (`jobs.py`/`worker.py`), backed by a new `jobs` table so status
  survives a worker restart. `analytics/query` stayed synchronous on purpose -- it's
  deterministic SQL with no LLM call, nothing to move.
- **Every LLM-calling route is cached** (`cache.py`'s `ResultCache`, Redis-backed) keyed on
  `(dataset_id, route, canonicalized request)`. A `Dataset` row is immutable once created, so a
  cache hit is correct indefinitely -- the TTL is memory hygiene, not correctness.
- **Rate limiting is Redis-backed and distributed** (`auth/rate_limit.py`'s `RedisRateLimiter`),
  covering `/auth/*` (keyed per client IP) and the three LLM routes above (keyed per project,
  since LLM spend is a tenant budget, not a per-client one).
- **Known, measured limitation:** a worker process that dies from an uncaught exception (or an
  RQ `job_timeout`) correctly marks its `Job` row `failed` with the real error -- verified against
  a real Groq 429. A worker killed with `SIGKILL` mid-job does not: the row is left stuck at
  `running` forever, confirmed by actually killing a real worker container mid-discovery-call
  and observing the stuck row. No heartbeat/staleness-reconciliation mechanism exists yet to
  reclaim it; that's explicitly deferred, not solved by this phase.
