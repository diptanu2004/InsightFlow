# InsightFlow — AI-Powered E-Commerce Analytics Platform

> Context file for Claude Code. Read this first before touching any code in this repo.
> Source of truth for the full architecture: `AI_Ecommerce_Analytics_Project_Architecture.md`
> in this project. Treat this file as a working summary, not a replacement for it.

---

## 1. What this project is

A schema-independent, AI-powered e-commerce analytics platform. A business uploads CSV/Excel
files with arbitrary column names (`net_amt`, `client`, `purchase_dt`, etc.), and the platform:

1. Automatically profiles and infers what each column *means* semantically (revenue, customer_id,
   transaction_date...).
2. Builds a canonical semantic model that's independent of the source schema.
3. Runs deterministic analytics on top of that model.
4. Auto-generates dashboards.
5. Answers natural-language business questions with verified numbers + AI explanations.
6. Optionally feeds the same semantic model into Power BI.

The differentiator is **schema adaptability** — no two businesses need the same column names for
the platform to understand them as the same underlying business concept.

---

## 2. Non-negotiable design philosophy

These three principles override any local implementation convenience. If a proposed change
violates one of these, flag it before proceeding:

1. **LLMs understand; deterministic systems calculate.**
   The LLM interprets intent, maps schemas, plans dashboards, explains results. It **never**
   produces the actual numbers — those always come from deterministic SQL/Python. This is the
   core anti-hallucination mechanism of the whole system.

2. **Semantic model is the central abstraction.**
   Raw messy schemas → Schema Discovery → Canonical Semantic Model → everything else (analytics,
   dashboards, chatbot, Power BI). Downstream components never reason about raw source columns.

3. **Structured intermediate representations over free-form generation.**
   - NL question → LLM Planner → Analytical AST/DSL → deterministic SQL compiler → validated
     read-only SQL → DB. Never "LLM → arbitrary SQL → execute."
   - Dashboard request → LLM → Dashboard JSON spec → deterministic React renderer. Never
     "LLM → React source code → execute."

**Explicit non-goals** (do not reintroduce these patterns):
- LLM doing raw arithmetic
- LLM-generated frontend code executed directly
- Blind execution of LLM-generated SQL
- Power BI as the core engine / source of metric truth
- Building auth + Redis + React + Power BI + AI agents all at once instead of POC-first

---

## 3. Tech stack

- **Backend:** Python, FastAPI, SQLAlchemy (or equivalent ORM), Pandas/Polars for profiling
- **AI:** LangChain, LLM provider abstraction, structured outputs/JSON schemas, selective RAG
- **DB:** PostgreSQL (app metadata, semantic models, metrics, dashboards, audit) — dev/free-tier
  options: Supabase or Neon
- **Cache/jobs:** Redis (caching, rate limiting, job coordination) + worker queue (Celery/RQ)
- **Frontend:** React, TypeScript, charting + dashboard component system (Phase 8, not yet built)
- **BI:** Power BI as a downstream consumer of the semantic model (Phase 9, not core)
- **Storage:** Object storage for raw CSV/Excel; Postgres for structured metadata only

---

## 4. Multi-tenant hierarchy

```
User → Organization/Workspace → Analytics Project → Data Sources/Datasets
     → Semantic Model → Metrics / Analytics / Dashboards / Queries
```

RBAC roles (planned): Owner, Admin, Analyst, Viewer. Every tenant-owned object must be scoped to
its organization/project — this is a Phase 6 concern but should inform data model decisions made
earlier (e.g. POC scaffolding) so retrofitting isn't painful.

---

## 5. Development order (deliberately incremental — do not skip ahead)

```
Phase 0  — Analytics fundamentals + manual dashboard exercise
Phase 1  — POC 1: Schema Discovery + Relationship Detection
Phase 2  — POC 2: Analytics Engine
Phase 3  — POC 3: Dashboard Generation
Phase 4  — POC 4: Natural Language Analytics Chatbot
Phase 5  — Integrate into FastAPI
Phase 6  — Authentication + Multi-tenancy
Phase 7  — Redis + Background Workers + Caching
Phase 8  — React Frontend
Phase 9  — Power BI Integration
Phase 10 — Security + Observability + Deployment
```

Rationale: validate every hard AI/system component in isolation (headless, CLI-driven, own venv)
before integrating. Do not build auth/Redis/React/Power BI alongside the POCs.

### Current status: POC 1–4 and Phase 5 are all done and closed.
Phase 6+ (auth, multi-tenancy, Redis, frontend, Power BI, deployment hardening) has not been
started. Phase 6+ work should still be explicitly requested, not assumed — confirm with the user
before starting it.

---

## 6. POC summaries

### POC 1 — Schema Discovery + Relationship Detection (done)
Given arbitrary CSVs (e.g. `orders.csv`, `customers.csv`, `products.csv` with unpredictable column
names), profile each column (type, null %, cardinality, sample values, etc.), infer semantic type
per column with a confidence score, and detect cross-file relationships (FK-like links) using
name similarity + type compatibility + value overlap + LLM reasoning as a supporting signal only
— never trusted blindly. Output: a canonical semantic model (`Customer`, `Order`, `Product`
entities). Low-confidence mappings surface for user confirmation.
Real integration test used Olist dataset.

### POC 2 — Analytics Engine (done)
Deterministic metric registry as source of truth (e.g. `Revenue = SUM(order.revenue)`,
`AOV = Revenue / Orders`). LLM plans an Analytical AST/DSL from NL; a deterministic compiler turns
that into validated, read-only SQL. LLM never invents formulas or executes raw SQL.
Shipped shape: 10 registered metrics, `MetricRegistry`, `AnalyticalQuery` AST,
`AnalyticsEnginePipeline`, `MetricResult` types — these are now stable interfaces POC 3/4 build on.
See `metrics_catalog.md` for the full metric backlog beyond these 10, classified by:
- **Bucket** (engineering cost): B1 (pure composition of existing measures) → B2 (needs one new
  registered measure) → B3 (needs a new compiler operator/primitive — window fns, HAVING,
  percentile, stddev, correlation) → B4 (structurally different metric family — multi-step,
  stateful, model-based; needs its own pipeline, not a registry row)
- **Data domain**: CORE (derivable from existing Order/Customer/Product model) vs. `EXT-*`
  (needs a new entity — marketing spend, web sessions, inventory, support tickets, loyalty,
  search logs, survey data, extra cost data — gated on schema-discovery work first, any bucket)

### POC 3 — Dashboard Generation (done)
Two-phase discipline mirrors "LLMs understand, deterministic systems calculate":
1. **Planning (LLM):** `DashboardPlanner` (one Groq call, structured output) consumes semantic
   model summary + registered metrics + a small fixed set of pre-computed diagnostic signals
   (revenue growth, top/bottom category or region) → emits a candidate `DashboardSpec`.
2. **Resolution (deterministic):** `DashboardValidator` rejects/repairs anything referencing an
   unregistered metric or nonexistent dimension; `DashboardDataResolver` builds one
   `AnalyticalQuery` per component and runs it through POC 2's existing pipeline, producing a
   `HydratedDashboard` (spec + real `MetricResult` per component).
`DashboardGenerationPipeline` orchestrates all of it. Code lives in
`poc3_dashboard_generation/src/insightflow_dashboard/dashboard/` (spec, signal, validator, resolver,
planner, planner_context, hydrated, pipeline) and `.../llm/` (vendored `LLMClient`/
`GroqLLMClient` from POC1).

v1 component types: `KPI`, `LINE_CHART`, `BAR_CHART`, `PIE_CHART`, `TABLE` only.
`HEATMAP`/`FUNNEL`/`COHORT`/`SCATTER` are named in the architecture doc but blocked on B3/B4 work
in POC 2 that hasn't happened yet — don't try to build these prematurely.

Explicit non-goals for POC 3: no React rendering (Phase 8), no NL dashboard requests (POC 4), no
persistence/`dashboards` table (Phase 5/6), no Power BI export (Phase 9), no "business type" input
(flagged as an unresolved product decision — see open question below).

Milestones (all complete): M1 DSL+validator (no LLM) → M2 data resolver (hand-built spec, no
planner) → M3 planner (ChatGroq structured output) → M4 real-data integration test against POC 1's
actual Olist output, verified with real Groq calls.

**Two real bugs found in the shared engine during POC 3's integration testing (both fixed, both
regression-tested):**
1. **Join fan-out** — grouping a `SUM` metric by a dimension reached via a one-to-many join could
   double-count. Fixed via `ROW_NUMBER()` dedup in `_compile_grouped_base`.
2. **`HAVING_RATIO` cross-entity grouping** — `repeat_purchase_rate` computed a structurally
   guaranteed 0.0 against real Olist data (grouped by the per-order surrogate `customer_id`
   instead of the real `customer_unique_id`). Fixed via new `group_by_entity`/
   `group_by_source_column` fields on `MetricDefinition`.

**Known, deliberately-deferred limitations (v2 backlog, not blockers):**
- `LINE_CHART` is modeled but not buildable — POC2's AST has no time-bucketing primitive.
- The Dashboard Spec DSL has no time-window/date-range filter at all (`FilterSpec` is just
  `{dimension, label}`, never applied to queries) — confirmed structural, not a prompt gap.
- `registry/bootstrap.py` duplication between POC2/POC3 (small, ~80 lines, not prioritized).

Docs: `poc3_dashboard_generation/docs/hld.md`, `docs/class_diagram.md`,
`docs/real_world_integration_test.md` (every real-Groq finding, numbered); each
`examples/*/README.md` has individual real-data/benchmark run results.

### POC 4 — Natural Language Analytics Chatbot (done — M1-M4 complete)
```
NL Question → Question Planner (LLM, not built yet) → QuestionIntent
  → Time Expression Resolver (deterministic) → Query Assembler (deterministic) → AnalyticalQuery(s)
  → Question Validator (wraps insightflow_core's ASTValidator) → AnalyticsEnginePipeline (unmodified)
  → QuestionResult → Insight LLM (not built yet) → Answer + Explanation
```
Depends on `insightflow_core` directly from day one (no fourth vendored copy), per the resolved
open decision in §7. Code lives in `poc4_nl_chatbot/src/insightflow_chatbot/models/` (`TimeExpression`,
`QuestionIntent`/`QuestionOperation`, `ResolvedQuery`, `CategoryDelta`/`QuestionResult`/`Answer`)
and `.../services/` (`TimeExpressionResolver`, `QueryAssembler`, `QuestionValidator`,
`ResultDiffer`, `QuestionExecutor`).

**Key design decision: relative dates resolve deterministically, never via LLM date arithmetic.**
The planner (once built) only ever picks from a closed `TimeExpression` enum (`LAST_QUARTER`,
etc.); `TimeExpressionResolver` turns that into concrete dates, seeded from the dataset's actual
min/max transaction dates — never wall-clock `today()` (Olist ends in 2018; "today" would silently
produce empty windows against real data).

**A real gap found while designing this POC, before code:** `insightflow_core`'s `AnalyticalQuery`
has no way to express "growth, grouped by dimension" on one AST node (`dimension` only valid with
`GROUP_BY`, `growth` only valid with `GROWTH` — mutually exclusive by the model's own validator).
So "which category caused the revenue decline" (the architecture doc's own POC 4 example) is
answered by decomposing into two ordinary `GROUP_BY` queries (one per period) run through the
unmodified engine, diffed per-category in plain deterministic Python (`ResultDiffer`, zero-fills a
category missing from either side, ranks by signed delta) — not a new engine primitive. Modeled as
`QuestionOperation.GROWTH_BY_DIMENSION`, a POC 4-level planning concept only.

Must explicitly detect and state when a question can't be answered from available data
(e.g. no session data → can't compute conversion rate) rather than hallucinating an answer —
checked twice (planner's own `answerable=False`, and `QuestionValidator` re-checking the
assembled AST against the live registry/semantic model regardless), same defense-in-depth
pattern as POC 2/3's validators.

**Milestones, all complete:** M1 DSL + deterministic time resolution + validator + executor → M2
`QuestionPlanner`/`InsightGenerator` (ChatGroq structured output) + `QuestionAnsweringPipeline`
orchestrator → M3 real-data integration test against POC 1's actual Olist output + a refusal
benchmark set → M4 real-Groq evaluation run against `data/refusal_benchmark.json`'s 16 fixtures
(model `openai/gpt-oss-120b`): **16/16 refusal accuracy, 8/8 intent accuracy, 8/8 plan accuracy,
4/4 numeric accuracy (where a scalar `expected_value` applies), 0 hallucinations.** Full writeup
in `poc4_nl_chatbot/docs/m4_evaluation_results.md`, including a real planner prompt bug M4 found
and fixed (a `HAVING_RATIO` metric was incorrectly refused for plain `aggregate` on one run — the
prompt only stated what's *disallowed* for `group_by`/`growth`, never explicitly confirmed any
metric is valid for plain `aggregate`; fixed with one clarifying sentence + a regression test).
188 tests passing repo-wide as of M4's completion.

**M3 found and fixed two real bugs in the shared `insightflow_core` engine** (regression-tested
there, applying to POC 2/3 too since it's shared code): (1) `ASTValidator` never checked whether a
plain `AGGREGATE`/`GROUP_BY` query's entity has a time field before applying a `time_filter` —
real Olist's `revenue` (on `payments`, no `transaction_date` column) crashed with an unhandled
`KeyError` instead of a clean rejection; fixed via a new `_validate_time_filter_entity_has_time_field`
check, generalizing the equivalent check `GROWTH` already had. (2) `QueryAssembler` was resolving
`TimeExpression.ALL_TIME` to an explicit bounded `TimeFilter` instead of "no restriction," which
made bug (1) worse than it needed to be; fixed by treating `ALL_TIME` and "no time expression"
identically. A third, deeper gap was flagged, then also fixed as its own deliberate follow-up:
`MetricResult` gained an explicit `shape: Literal["scalar", "grouped"]` field (sourced from
`CompiledQuery.result_shape`) so a legitimately-`NULL` scalar (a `GROWTH` query with a zero-count
comparison period) can be represented as `shape="scalar", value=None` instead of raising. This
touched all three POCs' shared contract — every hand-built `MetricResult` in test suites needed
`shape=` added, and POC 3's `SignalGatherer`/`DashboardPlanner` and POC 4's `InsightGenerator`
each had a latent bug (branching on value-presence instead of `shape`) fixed the same way. 187
tests passing repo-wide after this change. Full writeup in
`poc4_nl_chatbot/docs/class_diagram.md`'s "M3 real-data findings" section.

`QuestionAnsweringPipeline.answer()` is the entire integration surface, same convention as
`AnalyticsEnginePipeline.run()`/`DashboardGenerationPipeline.run()`: build a `PlannerContext` →
`QuestionPlanner.plan()` → refuse immediately if `answerable=False` → `QueryAssembler.assemble()`
→ `QuestionValidator.validate()` (wraps `ASTValidator`, refuses immediately if invalid, defense in
depth against a hallucinated intent) → `QuestionExecutor.execute()` (unmodified engine) →
`InsightGenerator.explain()`, only ever reached for a successfully-executed question.
`build_question_answering_pipeline()` wires all of this from just a semantic model, registry, a
real engine, and an `LLMClient`, inferring `reference_date`/`min_date` once via
`infer_date_bounds()` (reads the raw source CSV directly — deliberately not routed through the
shared engine, since its AGGREGATE path always casts results to `float`, not a fit for a date
column).

Docs: `poc4_nl_chatbot/docs/hld.md`, `docs/class_diagram.md` (both include a "resolved" section
covering the six implementation-blocking decisions made before code: category-set mismatch
handling, ranking convention, `TimeExpression` enum scope, growth comparison-period convention,
refusal benchmark timing, `reference_date` sourcing).

### Phase 5 — FastAPI Backend Integration (done, closed)
One FastAPI app (`backend/`, package `insightflow-backend`) wiring all four POCs +
`insightflow_core` behind HTTP routes — "wire routes to existing pipelines," no pipeline logic
rewritten.

**Blocking issue found and resolved first: package-name collision.** All four POCs had declared
the same top-level import package (`insightflow`), which prevented importing more than one into
a single process. Renamed each POC's `src/insightflow/` → a unique package, updating imports/
`pyproject.toml`/tests/scripts/examples in each:
- `poc1_schema_discovery` → `insightflow_schema_discovery`
- `poc2_analytics_engine` → `insightflow_analytics` (no pipeline class of its own anymore — its
  engine lives in `insightflow_core`; the backend does not depend on this package)
- `poc3_dashboard_generation` → `insightflow_dashboard`
- `poc4_nl_chatbot` → `insightflow_chatbot`
- `insightflow_core` — unchanged

Every POC's own test suite (plus `insightflow_core`'s) was re-verified green standalone
immediately after its rename, before the backend was built on top.

**Backend package** (`backend/src/insightflow_backend/`): `wiring.py` calls each POC's own
pipeline/builder unchanged (`SchemaDiscoveryPipeline.run()`, `insightflow_core.pipeline
.build_pipeline()`, `insightflow_dashboard`'s `build_dashboard_pipeline()`,
`insightflow_chatbot`'s `build_question_answering_pipeline()`); `session.py` holds one in-memory
`SessionState` in `app.state` (single global session — no DB/multi-tenancy yet, that's Phase 6)
with lazy-built + cached pipelines per uploaded dataset; `registry/bootstrap.py` is one canonical
copy of the registry (the three POC-local vendored copies were deliberately left in place — each
POC's own tests still import their own copy). Routes: `POST /schema/discover` (CSV upload only —
`insightflow_core`'s `QueryExecutor.register_sources` hardcodes a `.csv` lookup per entity
regardless of what POC 1's parser would otherwise accept), `POST /analytics/query`,
`POST /dashboard/generate`, `POST /chat/ask`, `GET /health`. Calling query/dashboard/chat before
any upload returns `409`, not a crash.

POC 1's own `SemanticModel` and `insightflow_core`'s (field-identical, not the same class — see
§7.1) are bridged via the same JSON round-trip every POC script already used, just done
in-memory in `wiring.run_schema_discovery()` instead of via a file.

**Verified end-to-end, not just unit-tested:** a real-data, real-Groq smoke test drives all 4
endpoints in sequence (upload → query → dashboard → chat) and passes; 194 tests green across all
6 packages; `docker build -f backend/Dockerfile -t insightflow-backend .` (root-context build,
needed since path deps require sibling POC directories) builds cleanly and the container serves
`/health`/`/docs`. Docs: `backend/docs/hld.md`, `docs/class_diagram.md`.

---

## 7. Open technical decisions (unresolved — surface before deciding unilaterally)

1. **Semantic model duplication — RESOLVED (option B).** `models/`, `compilation/`, `execution/`,
   `safety/`, `validation/`, `pipeline.py`, and `MetricRegistry` were extracted out of POC 2 into a
   new sibling package, **`insightflow_core/`** (`insightflow_core/src/insightflow_core/`). Both
   POC 2 and POC 3 depend on it via a local editable path dependency
   (`[tool.uv.sources]` in each `pyproject.toml`) — deliberately **not** a uv workspace, to
   preserve "each POC has its own isolated venv." `registry/bootstrap.py` (which metrics get
   registered) stayed duplicated in each POC on purpose — that's business config, not engine code.
   Both Dockerfiles now build from the top-level `InsightFlow/` directory (e.g.
   `docker build -f poc3_dashboard_generation/Dockerfile -t insightflow-poc3 .`), since the path
   dependency needs the sibling `insightflow_core/` folder in build context; a top-level
   `.dockerignore` replaced the old per-POC ones. **POC 4 should depend on `insightflow_core` from
   day one** rather than vendoring a fourth copy — see `insightflow_core/README.md` for rationale
   and how to wire it in.

2. **"Business type" as a dashboard-planning input.** Neither POC 1's semantic model nor POC 2's
   registry captures anything like "this is a fashion retailer" vs "electronics retailer," but
   the architecture doc's §13.2 data-aware dashboard generation assumes it as an input. Not solved
   in POC 3 v1. Two candidate approaches, neither committed to: (a) heuristic LLM inference from
   category/product-name text, (b) optional free-text field at project-creation time. This is a
   product decision as much as an engineering one — likely gets resolved once Phase 6's
   project-creation flow is actually designed.

---

## 8. Evaluation discipline

Every POC needs a measurable benchmark, not subjective judgment — this is an AI-heavy system and
each component should be graded against ground truth or a rubric:
- **POC 1:** schema mapping accuracy, relationship detection accuracy, confidence calibration,
  user correction rate — against ~100 datasets with known expected mappings/relationships.
- **POC 2:** numerical correctness, query correctness, metric correctness, execution latency —
  same question must always produce the same deterministic result.
- **POC 3:** dashboard usefulness, metric relevance, chart selection, absence of unsupported
  metrics (mechanically checked by the validator — should be 100% by construction), layout
  quality — graded against a small benchmark of semantic models with distinct characteristics
  (healthy growth, declining revenue, retention problems) via a short human-written rubric per
  case, since this dimension isn't fully automatable.
- **POC 4:** intent accuracy, analytical-plan accuracy, SQL execution accuracy, numerical answer
  accuracy, hallucination rate, latency.

---

## 9. Repo layout convention (POC 2 established this; POC 3 mirrors it)

```
poc{N}_{name}/
├── pyproject.toml       # uv-managed
├── Dockerfile
├── src/insightflow_{name}/   # renamed from src/insightflow/ during Phase 5 to resolve a
│   │                          #   top-level import collision once all POCs share one process —
│   │                          #   see §6's Phase 5 section for the full old->new name mapping
│   ├── models/          # pure pydantic types, fully typed, testable without an LLM
│   ├── services/         # LLM planners, validators, resolvers — isolated behind interfaces
│   └── pipeline.py       # single orchestrator class: Pipeline.run(...) -> Result
│                          #   e.g. AnalyticsEnginePipeline.run(), DashboardGenerationPipeline.run()
│                          #   this was the entire integration surface Phase 5's FastAPI wiring used
├── docs/
│   ├── hld.md
│   └── class_diagram.md
├── data/                 # sample/fixture data, reused across POCs where possible
└── tests/
```

Convention to preserve: **pure model layer first, services layer behind clear interfaces, one
orchestrator entrypoint per POC.** This is what let Phase 5's `backend/` package stay "wire
routes to existing pipelines" instead of a rewrite.

---

## 10. Working agreement for this session

- POC 1–4 and Phase 5 are all done and closed. Don't start Phase 6+ work (auth, multi-tenancy,
  Redis/background workers, React frontend, Power BI, deployment hardening) without the user
  explicitly asking for it first.
- Don't build POC 3 component types beyond `KPI`/`LINE_CHART`/`BAR_CHART`/`PIE_CHART`/`TABLE`
  without first building the B3/B4 primitives in POC 2 that back them.
- Don't silently resolve the two open technical decisions in §7 — surface them for a decision
  when they become blocking.
- Reuse existing types (`MetricResult`, `AnalyticalQuery`, `SemanticModel`, `HydratedDashboard`,
  `Answer`) rather than inventing parallel ones, per the established minimal-surface-area
  convention.
- Import paths changed in Phase 5 — each POC's own package is now `insightflow_schema_discovery`
  / `insightflow_analytics` / `insightflow_dashboard` / `insightflow_chatbot`, not `insightflow`.
  If you see `from insightflow.` anywhere it's stale and should be fixed, not copied.
