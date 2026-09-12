# POC 3 — Dashboard Generation

Turns POC 1's `semantic_model.json` + POC 2's `MetricRegistry` into a validated, data-populated
dashboard specification. An LLM (`DashboardPlanner`) decides which components go on the
dashboard; POC 2's own, unmodified `AnalyticsEnginePipeline` computes every number on it — this
POC adds no second arithmetic path. See `docs/hld.md` for the full design and
`docs/class_diagram.md` for every class/method; this README is setup + status.

## Status

**Implemented, real-Groq-tested at both real-data scales and against one of two synthetic
benchmark cases end to end — one benchmark write-up still outstanding before closure.** All
classes in
`docs/class_diagram.md` have real logic. `DashboardPlanner` has been run against a real Groq call
four times on the small (8-order) real Olist subset (the first three runs each hit a different
real bug in how the planner's context was built — an ambiguous dimension offered, then a
non-groupable metric offered, then a metric kind `DashboardDataResolver` can't resolve as any
component at all — all three fixed and regression-tested), then twice more against the *full*
~99.4k-order Olist dataset. The first full-scale run succeeded end to end (no crash, no rejection,
`revenue`/`orders` matching the real reference numbers exactly) but surfaced one more real
correctness bug: `repeat_purchase_rate` came back exactly 0.0, because its grouping key resolved to
a per-order surrogate `customer_id` instead of the real per-person `customer_unique_id` (see
`docs/real_world_integration_test.md`'s "Finding 5"). **Fixed** — two new optional
`MetricDefinition` fields let a `HAVING_RATIO` metric group by a joined entity and a specific
physical column; ported to POC 2's shared copy of the same code, same as the earlier "Finding 4"
join-fan-out fix. **Re-run and confirmed**: `repeat_purchase_rate` now comes back ≈3.1%, a real,
plausible number for this dataset, and that same run's narrative explicitly connected the decline
signals to the low repeat rate into a stated "sustainability risk" verdict. Separately, a real Groq
run against the `benchmark_retention_problem` synthetic case was mechanically flawless but exposed
a usefulness gap — the planner queried `repeat_purchase_rate` correctly but never connected it to
the healthy-looking growth signals in its narrative; `DashboardPlanner`'s prompt was fixed to name
that kind of tension, and **a second real Groq run of this exact case confirmed it**: the new
narrative states outright that "growth is driven by new customers rather than repeat business" —
see `examples/benchmark_retention_problem/README.md`'s "Results" section for the full before/after.
The `benchmark_declining_revenue` case's own real-Groq evaluation is still outstanding (a prior
run's findings were never captured in writing). Every
stage (`SignalGatherer`, `DashboardValidator`, `DashboardDataResolver`, the full
`DashboardGenerationPipeline`) is tested end-to-end against real DuckDB and the sample dataset,
with the planner stage exercised via a `FakeLLMClient` (the same swappable-`LLMClient` pattern
POC 1 established) in the automated test suite.

## Setup

```
uv sync
uv run pytest        # 46 passing tests in this POC: spec models, DashboardValidator's rejection
                      # paths (unknown metric/dimension, GROUP_BY on a non-BASE metric, a growth
                      # metric picked for any component, unsupported component type, duplicate
                      # KPIs, component-count bounds, filters), SignalGatherer against real DuckDB
                      # + the sample dataset (including the graceful-degradation case),
                      # DashboardDataResolver, DashboardPlanner's prompt construction via a
                      # FakeLLMClient (including the retention-vs-growth-tension instruction found
                      # via the benchmark_retention_problem real Groq run), full pipeline
                      # integration tests (a valid planner output, a validator-rejected one, and
                      # regression tests for three real bugs found via real Groq runs -- an
                      # ambiguous dimension, a non-groupable metric, and a non-resolvable growth
                      # metric, all offered to the planner before they should have been -- see
                      # examples/real_olist_integration/README.md), and the two synthetic
                      # benchmark datasets (declining-revenue, retention-problem -- see
                      # examples/benchmark_*/README.md) checked against independently-computed
                      # expected values. The engine-level regression tests for the cross-entity
                      # GROUP BY join-fan-out fix and the HAVING_RATIO cross-entity grouping fix
                      # (docs/real_world_integration_test.md's "Finding 4"/"Finding 5") moved to
                      # ../insightflow_core/tests/ along with the shared SQLCompiler code they
                      # exercise -- run those via `cd ../insightflow_core && uv run pytest` (41
                      # passing there, shared with poc2_analytics_engine).
```

## Running against a real LLM

```
export GROQ_API_KEY=...          # or put it in a .env file
uv run python scripts/run_poc3.py \
  --semantic-model data/semantic_model.json --data-dir data/raw/sample
```

This uses the hand-built sample dataset and the generic `bootstrap_registry()` — good for a quick
smoke test that `DashboardPlanner`'s real Groq call and structured-output parsing actually work.

For a real-data run, see `examples/real_olist_integration/` — a self-contained harness (own copy
of the real Olist data + a registry hand-configured for POC 1's actual, imperfect field mapping,
mirroring `poc2_analytics_engine`'s own real-data example) with `uv run python
examples/real_olist_integration/run_real_test.py`. Full writeup in
`docs/real_world_integration_test.md`; summary: a growth metric whose base measure's entity has no
time field of its own and two dimensions genuinely ambiguous in POC 1's real output (cleanly
rejected, not crashes), three real bugs in `DashboardGenerationPipeline`/`DashboardValidator`
found across three successive real Groq runs and fixed (ambiguous dimensions, non-groupable
metrics, and growth-kind metrics all used to be offered to the planner despite being unusable),
and — on the fourth run, which otherwise succeeded end to end — a real correctness bug, now
**fixed**: grouping a `SUM`-based metric by a dimension that requires joining to a
one-to-many-related entity used to silently inflate the total (fixed in `SQLCompiler` and ported
to POC 2, which shares the same file; re-verified against the real Olist data).

Two more example folders cover the remaining pre-closure work (see "POC 3 status" below):
`examples/benchmark_declining_revenue/` and `examples/benchmark_retention_problem/` (two
synthetic, deliberately-shaped datasets for `docs/hld.md`'s evaluation step — both cases have now
been run and evaluated against their rubrics: the retention-problem case passes all three checks
after a planner-prompt fix, re-confirmed on a second real Groq run; the declining-revenue case
passes numbers/unsupported-metrics/chart-selection but only partially passes usefulness, for a
structural DSL reason rather than a planner gap — see each folder's README for the full write-up),
and `examples/real_olist_full_scale/` (the same real-Groq-call path as above, at the full
~99.4k-order scale instead of 8 — now run, see `docs/real_world_integration_test.md`'s full-scale
section and "Finding 5").

## Notable implementation details

- **This POC depends on POC 2's entire engine, not just POC 1's `SemanticModel` shape — via a
  shared package, not a vendored copy.** `models/`, `registry/metric_registry.py`,
  `compilation/`, `execution/`, `safety/`, `validation/`, and `pipeline.py` used to be
  byte-for-byte copies of `poc2_analytics_engine`'s own files, hand-kept in sync. Reusing type
  definitions alone (POC 2's own pattern for POC 1's `SemanticModel`) wasn't enough here —
  `SignalGatherer` and `DashboardDataResolver` need to actually *run* real queries through the
  real validator/compiler/checker/executor chain, not just reference its types — so a full second
  copy was the original solution. After that copy required two manual bug-ports (the join-fan-out
  fix and the `HAVING_RATIO` cross-entity fix — see `docs/real_world_integration_test.md`), it was
  extracted into `insightflow-core`, a small local package both this POC and POC 2 install as an
  editable path dependency instead of each keeping their own copy. Cross-venv import still isn't
  possible (both POCs' own packages are still named `insightflow`, per top-level `README.md`'s "No
  shared virtual environment ... between POCs") — `insightflow-core` doesn't change that, it just
  means there's one copy of the engine instead of two. See `docs/hld.md`'s and
  `docs/class_diagram.md`'s "shared vendored dependency" open questions (now resolved) and
  `insightflow_core/README.md` for the full rationale. `registry/bootstrap.py` (which metrics get
  registered) deliberately stayed a small, low-priority duplication — that's business
  configuration, not shared engine logic.
- **`src/insightflow/llm/`** is likewise a vendored, unmodified copy of POC 1's `LLMClient` ABC +
  `GroqLLMClient` (ChatGroq, `json_mode` structured output) — `DashboardPlanner` depends on the
  same swappable interface POC 1's `SemanticMapper`/`RelationshipDetector` already proved out.
- **`src/insightflow/dashboard/`** is POC 3's only genuinely new code: `spec.py` (the planner's
  output shape), `signal.py` (`SignalGatherer`), `validation.py` (`DashboardValidator`),
  `hydrated.py` (`HydratedDashboard`), `resolver.py` (`DashboardDataResolver`), `planner.py`
  (`DashboardPlanner`), and `pipeline.py` (`DashboardGenerationPipeline` — the single entry point
  a future FastAPI route would call, same pattern as POC 1/POC 2's own pipelines).
- **`ComponentType.LINE_CHART` is modeled but not buildable.** POC 2's `AnalyticalQuery` has no
  way to express "group by month" (`GROUP_BY`'s dimension means a single-join categorical field,
  not a time bucket) — flagged prominently in `docs/class_diagram.md`'s banner note.
  `DashboardValidator` rejects any `LINE_CHART` component with `component_type_not_yet_supported`
  rather than let the planner emit something the resolver can never hydrate. v1 effectively ships
  `KPI`, `BAR_CHART`, `PIE_CHART`, `TABLE`.
- **Found while implementing `SignalGatherer`** (documented in its own module docstring): a
  revenue/order/customer *growth* signal compares two calendar windows relative to a
  `reference_date` — defaulting to wall-clock "today" for real usage, since POC 2 has no
  `MAX(date)` capability to derive "the dataset's own latest date" from. Against a static,
  historically-dated sample dataset (this POC's own `data/`, dated Nov 2025 – Feb 2026) an
  unparameterized "today" reference would put the comparison period entirely outside the data,
  making the growth ratio's denominator zero — which is *also* POC 2's own known open gap
  (`poc2_analytics_engine/docs/class_diagram.md` closure finding 8: a genuinely-undefined ratio has
  no representation and crashes `MetricResult`, rather than returning `None` cleanly). `tests/`
  passes an explicit `reference_date` matching the sample data's own range so growth signals are
  meaningful and deterministic in CI; `SignalGatherer.gather()` also wraps every signal query in a
  try/except so one infeasible or crashing signal (missing time field, undefined ratio, or any
  other real gap in the underlying engine) never takes down the whole dashboard generation run —
  it's simply omitted from the signal list the planner sees.
- **`data/`** reuses POC 2's own small hand-built sample dataset (3 CSVs + `semantic_model.json`)
  unmodified — same known dataset, so results here are directly comparable to POC 2's own fixtures.

## POC 3 status: closed

The `GROUP BY`-fan-out correctness bug (`docs/real_world_integration_test.md`'s "Finding 4") is
fixed and re-verified against real Olist data at both the small and full scale — see that doc and
`poc2_analytics_engine/docs/class_diagram.md`'s closure-notes item 13 for the shared-code side of
the fix. The full-scale Olist run (`examples/real_olist_full_scale/`) has now actually happened
(not just been smoke-tested against the small subset): `revenue`/`orders` matched the real
reference numbers exactly, the vendored engine and `DashboardValidator` held up at ~99.4k rows with
no crash or rejection, and it surfaced one more real correctness bug —
`repeat_purchase_rate` computing exactly 0.0 against real data, root-caused to the
`customer_id`/`customer_unique_id` surrogate-key gap and now fixed (`docs/real_world_integration_test.md`'s
"Finding 5", ported to POC 2's shared copy the same way Finding 4 was) — **and re-run a second
time to confirm it**: `repeat_purchase_rate` now comes back ≈3.1%, a real number instead of a
structurally-guaranteed zero, and that run's narrative explicitly reconciled the decline signals
with the low repeat rate into a stated verdict rather than listing them independently.

The two benchmark semantic models `docs/hld.md`'s evaluation section called for
(`examples/benchmark_declining_revenue/`, `examples/benchmark_retention_problem/`) are built, and
both are now fully evaluated end to end. The retention-problem case's first run was mechanically
flawless (every number matched `expected_values.json` exactly, no unsupported metrics slipped
through) but exposed a real usefulness gap the case was designed to catch — the planner queried
`repeat_purchase_rate` (the mechanical part of the test) but its narrative never named the tension
between that low repeat rate and the healthy-looking growth signals. `DashboardPlanner`'s prompt
was fixed to reconcile that kind of tension rather than describe conflicting signals as
independently good news, and **a second real Groq run of this exact case confirmed the fix**: the
new narrative states outright that revenue/orders/customers are all up ~40% but "growth is driven
by new customers rather than repeat business," with numbers still matching
`expected_values.json` exactly — see `examples/benchmark_retention_problem/README.md`'s "Results"
section. All three rubric checks (unsupported metrics, numbers, usefulness) now pass on this case.

The declining-revenue case has also been run (one real Groq call): numbers matched
`expected_values.json` exactly, absence-of-unsupported-metrics and chart-selection both passed, and
the narrative correctly quantified the decline with a genuine, signal-grounded reconciliation
(negative growth vs. a healthy repeat-purchase rate) — but it never named Electronics as the
disproportionate driver, the one thing this case's shape was built to test for. Investigated and
found to be **structural, not a planner/prompt gap**: nothing in the Dashboard Spec DSL
(`ComponentSpec`/`FilterSpec`, `src/insightflow/dashboard/spec.py`) can scope a
`BAR_CHART`/`PIE_CHART`/`TABLE` component to a recent time window — `time_grain` only applies to
the not-yet-buildable `LINE_CHART`, and `FilterSpec` carries only `{dimension, label}` with no
value/range that `DashboardDataResolver` ever applies to a query — so a category breakdown is
necessarily a whole-period snapshot, which can't reveal a share-shift-over-time story. Same root
cause as the already-documented "no time-bucketing primitive in POC 2's AST" limitation. Full
write-up in
`examples/benchmark_declining_revenue/README.md`'s "Results" section — 2 of 3 rubric checks pass
cleanly, usefulness is a partial pass for a now-documented structural reason.

The one remaining open design question — vendor a third copy of POC 2's engine into POC 4, or
extract a shared package first — is now resolved: `models/`, `compilation/`, `execution/`,
`safety/`, `validation/`, and `pipeline.py` were extracted into `insightflow-core`, installed as a
local editable dependency into both this POC's and POC 2's still-fully-independent venvs (see
"Notable implementation details" above and `insightflow_core/README.md`). Both POCs' full suites
were re-run after the move with no regressions (`insightflow_core`: 41 passing;
`poc2_analytics_engine`: 16 passing; this POC: 46 passing), and POC 2's own CLI was re-verified
end to end against the shared package (`uv run python scripts/run_poc2.py --fixtures ... ` — still
10/10 metrics passing). A POC 4 built against this same package from day one, rather than
vendoring a fourth copy, is now the cheapest path forward — see that package's README for exactly
how to depend on it.

**POC 3 is closed.** The vendored engine holding up at real row counts, both fixed correctness
bugs (join fan-out, `repeat_purchase_rate`), the planner-prompt reconciliation fix, and both
benchmark cases' full rubric evaluations are all confirmed working end to end against real Groq
calls; the only remaining architectural question (shared engine vs. per-POC vendoring) is now
resolved rather than deferred. What's left is entirely optional, forward-looking work, not a
closure blocker: the declining-revenue case's Electronics-attribution gap is a documented,
deliberately-deferred v2 item (needs a time-window/date-range predicate added to the Dashboard
Spec DSL — an AST/DSL-level change, not a same-session patch), and `registry/bootstrap.py`'s
small remaining duplication with POC 2 (business configuration, not engine code) is noted but not
prioritized.
