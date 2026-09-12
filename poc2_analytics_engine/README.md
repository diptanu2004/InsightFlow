# POC 2 — Analytics Engine

Deterministic analytics engine for InsightFlow. Given POC 1's `semantic_model.json` and a
structured query (the AST), compiles it to SQL, runs it against DuckDB, and returns a verified
number — with no LLM involved anywhere in this POC. See `docs/hld.md` for the full design and
`docs/class_diagram.md` for every class/method; this README is just setup + status.

## Status

**Closed.** See "POC 2 status: closed" at the bottom of this file for the full closure note and
backlog. Every class in `docs/class_diagram.md` is fully written — `ASTValidator`,
`FieldResolver`, `SQLCompiler`, `SQLSafetyChecker`, `QueryExecutor`, and `Evaluator` all have real
logic, not stubs. All ten `docs/supported_metrics.md` metrics are registered
(`insightflow.registry.bootstrap_registry`) and run correctly against a real sample dataset
(`data/`) through the full `validate → compile → check → execute` pipeline via real DuckDB —
including the join needed for regional performance, the growth operator, and the HAVING/threshold
operator behind `repeat_purchase_rate` — and were separately verified end-to-end against real POC 1
output over the real, full-scale (~99.4k order) Olist dataset. See `docs/class_diagram.md`'s
"Decisions made during implementation" for what changed from the pre-implementation sketch, and its
"Notes" section for thirteen real gaps found in total (across real-data testing, a final closure
sweep of every doc and source file, and one more found post-closure — see below) — worth reading
before trusting this engine against new real data for the first time.

**Post-closure fixes**: two real Groq runs against real Olist data from
`poc3_dashboard_generation` (at the time, vendoring this engine unmodified) surfaced two real
correctness bugs in this file's own `SQLCompiler` — grouping a `SUM`-based metric by a dimension
reached via a cross-entity join could silently multiply the total when that join fanned out
(`docs/real_world_integration_test.md`, "Finding 8"; `docs/class_diagram.md`'s closure-notes item
13), and `HAVING_RATIO`'s grouping had no way to reach a real per-person identity one join away,
so `repeat_purchase_rate` silently computed 0.0 (item 14). Both fixed here, and — at the time —
manually ported into POC 3's vendored copy.

**Shared-package extraction**: that manual porting (twice, both real bugs) is exactly what
motivated pulling this engine out into its own package. `models/`, `compilation/`, `execution/`,
`safety/`, `validation/`, and `pipeline.py` no longer live under `src/insightflow/` in this repo —
they now live in `../insightflow_core`, installed as a local editable dependency into this POC's
own (still fully separate) venv. See `docs/class_diagram.md`'s note right after "Ownership
boundary" and `insightflow_core/README.md` for the full rationale; POC 3 depends on the same
package instead of vendoring a copy, and any future POC should too.

## Setup

```
uv sync
uv run pytest        # 16 passing tests in this POC: registry/bootstrap-dependent integration
                      # tests (test_engine_integration.py, test_ratio_and_time_filter_fixes.py)
                      # against the real sample dataset in data/raw/sample. The engine-level unit
                      # tests (models, FieldResolver, ASTValidator, SQLSafetyChecker, the
                      # join-fan-out and HAVING_RATIO regression tests) moved to
                      # ../insightflow_core/tests/ with the code they exercise -- run those via
                      # `cd ../insightflow_core && uv run pytest` (41 passing there).
```

## Notable implementation details

- **`models/semantic_model.py` (now in `insightflow_core/`) is a hand-kept copy of POC 1's
  `SemanticModel` shape, not a real import.** POC 1 is a genuinely separate, isolated venv
  (top-level `README.md`: "No shared virtual environment or dependency conflicts between POCs"),
  so there's still no cross-venv import path back to it. What changed: POC 2 and POC 3 used to
  each keep their own separate mirror of this file; now they share one, in `insightflow_core/`
  (see its own README). If POC 1's shape changes, that one file still needs updating by hand — see
  its own docstring and `docs/class_diagram.md`'s "Ownership boundary" note.
- **No LLM dependency, on purpose** — `pyproject.toml` deliberately has no `langchain`/
  `langchain-groq`. If one shows up here later, that's scope drift, not a missing dependency
  (`docs/hld.md`: "No LLM dependency").
- **Known POC2-only simplifications**, both flagged in code comments and in
  `docs/class_diagram.md`: exactly one canonical time field per entity (`transaction_date`, POC
  1's own vocabulary name) for every `time_filter`/`growth` query, and one `source_file` per
  entity in `QueryExecutor.register_sources` (an entity spanning multiple CSVs isn't handled).
  Both are tracked as v2 concerns alongside multi-hop joins, not solved here.
- **`data/`** holds a small hand-built sample dataset (3 CSVs + `semantic_model.json`) with
  known, hand-computed expected values — `data/fixtures/*.json` and
  `tests/test_engine_integration.py` both check against the same numbers.
- **`examples/real_olist_integration/`** and **`examples/real_olist_full_scale/`** are two rounds
  of testing against *real* POC 1 output feeding *real* Olist data (not hand-built sample data on
  either side) — see `docs/real_world_integration_test.md`. Together they found and fixed six real
  gaps (four in how POC 2 reads POC 1's output, two in `SQLCompiler` itself — the second pair only
  surfaced at real ~99.4k-order scale, not the small referential sample) plus one deliberately
  left open. `tests/test_ratio_and_time_filter_fixes.py` locks in the two `SQLCompiler` fixes with
  small, targeted fixtures shaped like the real gaps that found them.
- **A closure sweep** (every doc and source file checked against each other and against actual
  runs, not a new dataset) found five more real gaps — a crash (`SUM` over zero matching rows),
  two silent-wrong-answer cases (`GROUP_BY` and `time_filter` silently accepted, then ignored, on
  metric kinds that don't support them), and two documentation/packaging inconsistencies
  (`hld.md`'s stale "not yet implemented" banner; the Dockerfile never actually copying `data/`
  into the image it's documented to need). All fixed; see `docs/class_diagram.md`'s "Findings from
  the POC 2 closure sweep" for exactly what each fix does and doesn't cover — two of the five
  fixes make previously-silent wrong behavior fail loudly instead of actually building the missing
  capability (grouped/time-filtered `RATIO`-family metrics), which is carried forward as backlog
  below, not hidden by the cleaner error message.

## Usage

```
uv run python scripts/run_poc2.py \
    --query data/queries/revenue.json \
    --semantic-model data/semantic_model.json \
    --data-dir data/raw/sample

# or run the evaluation harness over a fixtures directory:
uv run python scripts/run_poc2.py --fixtures data/fixtures/ \
    --semantic-model data/semantic_model.json --data-dir data/raw/sample
# -> Metrics -> 10/10 passed
```

Mirrors `poc1_schema_discovery/scripts/run_poc1.py`'s convention: this CLI does exactly what a
future FastAPI route will do, so wiring the route later is a thin wrapper, not a rewrite.

## Docs

- `docs/hld.md` — pipeline design, the two-tier Metric Registry, all settled decisions
- `docs/class_diagram.md` — every class and method, with the reasoning behind non-obvious calls
  and what changed during implementation
- `docs/supported_metrics.md` — the ten metrics this POC targets, and build order
- `docs/metrics_catalog.md` — the v2 platform backlog this registry grows into later
- `docs/real_world_integration_test.md` — both rounds of the real-Olist-data / real-POC1-output
  test: dataset provenance, registry judgment calls, results, and all seven gaps they found

## POC 2 status: closed

Closed after all ten target metrics were implemented and verified three separate ways: against a
hand-built sample dataset with hand-computed expected values (`data/`, `tests/`), against real
POC 1 output over a small real Olist subset (`examples/real_olist_integration/`), and against that
same real POC 1 mapping over the full, real ~99.4k-order Olist dataset
(`examples/real_olist_full_scale/`) — plus a final closure sweep checking every doc and source
file for coherence rather than assuming it. That progression found twelve real gaps in total (five
in how POC 2 reads real POC 1 output, two `SQLCompiler` bugs invisible below real row counts, five
more from the closure sweep) — eleven addressed at some level, one left fully open — which is a
materially different, more trustworthy result than "all tests pass" against fixtures nobody
adversarially checked against real data or against each other.

**Post-closure, a thirteenth gap surfaced**: `poc3_dashboard_generation` vendors this engine
unmodified and, via a real Groq-driven planner against the same real Olist data, hit a
`SQLCompiler` join-fan-out bug this POC's own tests never had a fixture shaped to expose (every
cross-entity `GROUP BY` tested here joins in the direction that happens not to fan out). Fixed in
both POCs at once, since it's shared code — see `docs/real_world_integration_test.md`'s "Finding
8" and `docs/class_diagram.md`'s closure-notes item 13 for the full writeup. "Closed" above still
stands: this is the same "close, then keep testing against reality, then fix what reality finds"
discipline this whole doc describes, not a reopening.

**Later still, a fourteenth gap** (same pattern, one layer up again): `HAVING_RATIO`'s grouping
had no way to reach a real per-person identity one join away, so `repeat_purchase_rate` computed a
structurally-guaranteed 0.0 against real Olist data. Fixed the same way — see
`docs/real_world_integration_test.md`'s "Finding 9" and `docs/class_diagram.md`'s closure-notes
item 14.

**After both of those, this engine was extracted into its own package** (`../insightflow_core`)
instead of continuing to hand-vendor it into POC 3 — see `docs/class_diagram.md`'s note right
after "Ownership boundary" and this file's "Shared-package extraction" note above. Two real bugs
requiring a manual second port was the concrete cost that made the extraction worth doing before a
POC 4 would have made it a third copy.

**Carried forward as backlog, not blocking POC 3:**
- `revenue_growth` can't actually be computed — its base measure (`revenue`) lives on an entity
  (`payments`) with no date column in the real Olist schema, and `SQLCompiler._compile_growth`
  doesn't support joining to a different entity to borrow one. Cleanly rejected today, not
  crashing; real fix needs single-hop cross-entity time filtering for `GROWTH`, the same class of
  work as the multi-hop-joins simplification below (found via `examples/real_olist_integration/`)
- `RATIO`/`GROWTH`/`HAVING_RATIO` metrics don't support `GROUP_BY`, or (for `HAVING_RATIO`
  specifically) `time_filter` — both now cleanly rejected rather than silently ignored, but the
  actual capability (a grouped AOV-by-category, a time-filtered repeat purchase rate) isn't built
  (found via the closure sweep)
- A scalar result that's genuinely undefined — a `RATIO`/`HAVING_RATIO` whose denominator is 0
  after filtering, e.g. AOV over a period with zero orders — still crashes with an unhandled
  pydantic `ValidationError`. `MetricResult`'s "`value` or `rows`, never neither" invariant has no
  way to represent "this scalar is legitimately undefined," and there's a test explicitly asserting
  that invariant (`test_metric_result_rejects_neither_value_nor_rows`), so relaxing it wasn't done
  unreviewed during a closure pass (found via the closure sweep)
- Ambiguous dimension resolution (real Olist `category`: `products` *and* `category_translation`;
  `region`: `customers` *and* `sellers`) is cleanly rejected, not resolved — real fix needs either
  qualified dimension names on the AST (`"products.category"`) or a POC 1 vocabulary refinement,
  and is a genuine two-POC design decision, not just a POC 2 patch (found via
  `examples/real_olist_integration/`)
- Multi-hop joins, one canonical time field per entity, and one `source_file` per entity are all
  POC 2 simplifications settled *before* implementation (`hld.md`), not discovered gaps — carried
  forward here for completeness since they still constrain what POC 3 can assume this engine
  handles
- POC 1's `SemanticModel` doesn't record a field's original file format anywhere, so POC 2 always
  assumes `.csv` in `QueryExecutor.register_sources` — untested and would silently break against
  an Excel-sourced semantic model (POC 1's own `FileParser` accepts `.xlsx`)
- Query execution has a configurable timeout (`QUERY_TIMEOUT_SECONDS`) that is never actually
  enforced — DuckDB's Python API has no portable per-query wall-clock timeout, and building one
  wasn't attempted; honestly flagged in `QueryExecutor`'s own code rather than silently absent
- `MetricFixture.dataset_dir` is set in every fixture JSON but never read — `scripts/run_poc2.py
  --fixtures` always runs every fixture against the CLI's single shared `--data-dir`. Harmless
  today (nothing points elsewhere), silently misleading if a future fixture author expects
  otherwise (found via the closure sweep)
- The Dockerfile fix (`COPY data ./data`, `.dockerignore` no longer excluding it) is based on
  static reading of both files, not a verified `docker build` — no Docker daemon is reachable from
  this development environment
- Untested: a semantic mapping with categories of imperfection the real Olist data didn't happen
  to contain (e.g. a field mapped to an entirely *wrong* canonical name, not just an ambiguous or
  missing one), and query latency under concurrent/repeated load rather than one query at a time

None of these were fixed reactively mid-closure on the theory that patching each one as found
risks tuning the code to whatever gap was just found rather than fixing the underlying design —
e.g. the two `GROUP_BY`/`time_filter` gaps got a validation guard (fail loud, not silently wrong)
rather than a rushed compiler change, and the `MetricResult` "undefined scalar" gap was left alone
entirely rather than relaxing a deliberately-written invariant under time pressure. Same practice
POC 1's own closure followed.
