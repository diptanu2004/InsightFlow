# insightflow-core

The shared analytics engine — `models`, `compilation`, `execution`, `safety`, `validation`,
`pipeline.py` (`AnalyticsEnginePipeline` + `build_pipeline`), and the generic `MetricRegistry`
storage class — extracted out of `poc2_analytics_engine` so `poc3_dashboard_generation` (and any
future POC that needs a real running analytics engine, not just its type shapes) depends on ONE
copy of this code instead of hand-vendoring a second one.

## Why this exists

`poc3_dashboard_generation` used to vendor this entire engine byte-for-byte (with a provenance
header on every file) because cross-venv imports between POCs aren't possible — every POC is an
isolated venv, and POC 2's and POC 3's own top-level packages are both named `insightflow`, so
`import insightflow` from inside POC 3 could never reach POC 2's real installed package. That
vendoring worked, but every real bug this engine had came with a tax: fix it once, then remember
to port the exact same fix into the other copy by hand. That happened twice in this project's own
history (the cross-entity `GROUP BY` join-fan-out fix, and the `HAVING_RATIO` cross-entity
grouping fix) — both required a second, manual, byte-for-byte port into the vendored copy, and
both are called out in `poc2_analytics_engine/docs/class_diagram.md`'s and
`poc3_dashboard_generation/docs/class_diagram.md`'s closure notes as exactly the kind of drift
risk this package removes.

This package is that fix: one real copy of the engine, one test suite for it (`tests/`, moved
here from `poc2_analytics_engine/tests/` — the tests that exercised these modules directly,
independent of POC 2's own specific registered metrics), installed as a local **editable path
dependency** into each POC's own, still-completely-separate venv.

## What stays OUT of this package, deliberately

- **Each POC's own `bootstrap_registry()`** (`registry/bootstrap.py`, kept in each POC). *Which*
  `MetricDefinition`s/`Measure`s get registered is business configuration, not shared engine
  logic — POC 2 and POC 3 happen to register the identical ten metrics today (POC 3 reuses POC
  2's own sample dataset unmodified), so this one file is still a small, deliberate, low-priority
  duplication (~80 lines) left for a future pass, not folded in here. A real per-business
  deployment would need this to differ per POC/tenant anyway, which is exactly why it's kept as
  the POC's own configuration rather than shared code.
- **Any POC-specific config** (`insightflow/config.py` — DuckDB path, timeouts, row limits, env
  vars). `build_pipeline()` here takes these as plain keyword arguments with the same defaults
  POC 2's config has always used, rather than reaching into a caller's config module — see that
  function's own docstring for why (this package has zero runtime dependency on any POC's code;
  earlier draft of this extraction had it reach into `insightflow.config.settings` via a lazy
  import, which worked for each POC at runtime but meant this package's OWN test suite couldn't
  exercise `build_pipeline()` without a POC also being installed — not actually pluggable).
- **Anything LLM-related** (POC 2 has no LLM dependency by design; POC 3's `DashboardPlanner`/
  `GroqLLMClient` stay POC 3's own).
- **POC 1's `SemanticModel`-producing pipeline itself** — `models/semantic_model.py` here is
  still a hand-kept *mirror* of POC 1's shape (POC 1 remains a genuinely separate, isolated venv
  this package can't depend on), same as it always was. What changed is that POC 2 and POC 3 now
  share this one mirror instead of each keeping their own — see that file's own docstring.

## How a POC depends on this

Each POC's `pyproject.toml`:

```toml
[project]
dependencies = [
    ...,
    "insightflow-core",
]

[tool.uv.sources]
insightflow-core = { path = "../insightflow_core", editable = true }
```

This is a **plain local path dependency, not a `uv` workspace**. That distinction matters here
specifically because of a real constraint the top-level `README.md` has stated since POC 1: *"No
shared virtual environment or dependency conflicts between POCs."* A `uv` workspace would merge
this package's and every POC's lockfile/venv into one — exactly the thing that constraint rules
out, and the reason POC 3 vendored a whole copy of this engine in the first place instead of just
depending on POC 2 directly. A path dependency doesn't do that: `uv sync` inside
`poc2_analytics_engine/` (or `poc3_dashboard_generation/`) still creates and manages that POC's
own fully independent `.venv` and `uv.lock`; it just installs this package into that venv,
editable, from its source on disk instead of hand-copying that source a second time. Each POC
stays exactly as independently runnable/Docker-deployable as before — see the note below on what
did have to change for Docker specifically.

## Docker

Because the dependency is a relative path (`../insightflow_core`), a POC's own directory is no
longer a sufficient Docker build context by itself. Both `poc2_analytics_engine/Dockerfile` and
`poc3_dashboard_generation/Dockerfile` now build from the top-level `InsightFlow/` directory
instead:

```
docker build -f poc2_analytics_engine/Dockerfile -t insightflow-poc2 .
docker build -f poc3_dashboard_generation/Dockerfile -t insightflow-poc3 .
```

(run from the `InsightFlow/` root). Each Dockerfile `COPY`s this package in at the same relative
position (`/insightflow_core`, sibling of `/app`) that it has on disk, so the editable path
resolves identically inside the image. See `.dockerignore` at the top level (replacing the
per-POC ones, since Docker only ever reads the context root's `.dockerignore`).

## Extending this for a future POC (the "make Phase 5 minimum effort" goal)

A future POC that needs to run real `AnalyticalQuery`s against real data (POC 4's NL chatbot,
most obviously) should depend on this package the exact same way POC 2 and POC 3 do — add the
`[project.dependencies]` + `[tool.uv.sources]` block above, `uv sync`, and get a battle-tested,
already-fixed engine for free, instead of hand-vendoring a third copy. When Phase 5 (architecture
doc §29) packages every POC behind one FastAPI app, this package is what that app's own service
layer should import directly — the whole point of extracting it now, while it was still just two
duplicate copies and a well-tested one-time move, rather than waiting until there were three (or
more) copies and a bigger integration push happening at the same time.

## Running this package's own tests

```
uv sync
uv run pytest -q
```

Fully self-contained — no `GROQ_API_KEY`, no other POC's venv, no network access needed. `data/`
here holds a copy of the same small sample `semantic_model.json` POC 2 ships, just enough for the
tests that need a real `SemanticModel` fixture (`test_field_resolver.py`, `test_sql_safety_checker.py`).
