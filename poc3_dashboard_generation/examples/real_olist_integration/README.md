# Real-world integration test: real Olist data, real Groq call

Everything under `data/` is an **unmodified copy** of
`poc2_analytics_engine/examples/real_olist_integration/data/` — which is itself a byte-for-byte
copy of POC 1's actual `olist_model.json` output plus the real, unmodified Olist e-commerce CSVs.
Copied rather than referenced across POC folders, per the top-level `README.md`'s "self-contained
POC" rule — nothing here reads another POC's files at runtime. See that POC 2 folder's own
`README.md` and `../../docs/real_world_integration_test.md` (POC 2's) for full dataset provenance
and the five POC1→POC2 gaps it originally surfaced.

`run_real_test.py`'s registry (`bootstrap_real_business_registry()`) is copied unchanged from that
same POC 2 example — same judgment calls (which of the two "revenue"-mapped columns to trust,
the `customer_id`/`customer_unique_id` collision), because this is the same POC 1 output feeding
the same vendored engine. POC 3's own generic `registry.bootstrap.bootstrap_registry()` cannot be
reused here: it's shaped for POC 3's own hand-built sample dataset (`entity="orders"` for
revenue/customers), not this real one (real revenue lives on `payments`).

## Run it

```
export GROQ_API_KEY=...     # or rely on the .env file already in poc3_dashboard_generation/
uv run python examples/real_olist_integration/run_real_test.py
```

No `--semantic-model`/`--data-dir` flags — the script points at its own `data/` directory, same
convention as POC 2's equivalent script.

## What this test found (real gaps, not fixed here)

Run against this real (small, 8-order) subset with `SignalGatherer`'s `reference_date` pinned to
the dataset's own latest order date (2018-08-03 — see `run_real_test.py`'s `REFERENCE_DATE`
comment for why a wall-clock default doesn't work here), only **5 of the 8 candidate signals**
came back; the other 3 were cleanly dropped by `SignalGatherer`'s try/except, each for a distinct,
real, previously-undocumented reason:

- **`revenue_growth` failed validation**: the `revenue` measure's base entity is `payments`
  (see the registry rationale above), and `payments` has no `transaction_date` field of its own —
  POC 2's `ASTValidator` correctly rejects filtering a growth window on a field the measure's
  entity doesn't have. `order_growth`/`customer_growth` succeed because their base measures live
  on `orders`, which does have `transaction_date`. **This is a real, general limitation**: any
  measure registered against an entity without its own time field can never support `GROWTH`,
  regardless of whether a join *could* reach a time field on a related entity — POC 2's compiler
  does not join for growth-period filtering. Not a POC 3 bug; inherited from POC 2's engine and
  newly surfaced by this real registry (POC 3's own sample dataset's `revenue` measure happens to
  live on `orders`, which does have a date field, so this never showed up before).
- **`category` and `region` dimension signals failed validation**: both are genuinely ambiguous in
  POC 1's real output (`category` maps to both `products` and `category_translation`; `region` to
  both `customers` and `sellers`) and are cleanly rejected as "exists in more than one entity" —
  the exact same ambiguity POC 2's own `run_real_test.py` already documented and expected. Not a
  new finding, but confirms `SignalGatherer`'s dimension signals degrade the same safe way POC 2's
  own ad hoc queries do.

The 5 signals that *did* come back are correct against this subset: `total_revenue=729.39`,
`total_orders=8`, `total_customers=8` (POC 1's per-order-surrogate definition — see the registry
comment), `order_growth=2.0`, `customer_growth=2.0` (both doubled between the two 90-day windows,
correctly small-sample-noisy given only 8 orders).

## Real bug found on the first real Groq run (fixed)

The user's first real run (real `GROQ_API_KEY`, this real dataset) got all the way through
`SignalGatherer` and a real Groq call — `DashboardPlanner` returned a structurally valid
`DashboardSpec` — but `DashboardGenerationPipeline.run()` then raised:

```
ValueError: invalid dashboard spec: ['"category" exists in more than one entity ([\'products\',
\'category_translation\']) ...', '"region" exists in more than one entity ([\'customers\',
\'sellers\']) ...']
```

Real bug, not an LLM mistake: `DashboardGenerationPipeline._build_planner_context` listed
*every* canonical field name across all entities as an "available dimension" for the planner to
use, including ones that are genuinely ambiguous across two entities — POC 1's real output has
several (`category`, `region`, `product_id`, `revenue`, `transaction_date`, `customer_id`,
`order_id`). The planner had no way to know `category`/`region` were unsafe, picked them for a
`bar_chart`/`table`, and `DashboardValidator` (correctly) rejected the whole spec — but only
*after* burning a real LLM call. **Fixed**: `_build_planner_context` now filters
`available_dimensions` through the exact same `FieldResolver.find_entity_for_field` check
`DashboardValidator` already uses, so the planner is never offered a dimension the validator
would reject anyway (`DashboardGenerationPipeline` gained a `field_resolver` constructor
parameter to do this). A regression test
(`test_planner_context_never_offers_a_dimension_that_the_validator_would_reject_as_ambiguous` in
`tests/test_pipeline_integration.py`) covers this using the sample dataset's own `customer_id`
collision (`orders` and `customers` both have a `customer_id` field) — full suite is now 35
passing tests.

**Side effect worth knowing before re-running**: after this fix, POC 1's real output for this
dataset is dimension-poor — `price` is the *only* canonical field name that maps to just one
entity here, so it's the only dimension the planner will ever be offered for a `bar_chart`,
`pie_chart`, or `table` component against this real semantic model. Every other real field name
collides across at least two entities. This isn't a bug to fix here (the collisions are real,
inherited from POC 1's output, and the ambiguity check itself is correct) — just an expected
consequence of running against messier real data than the hand-built sample dataset, worth
knowing before judging the planner's next real output.

## Second real bug found on the re-run (fixed)

After the fix above, the real Groq call succeeded again and got further, but
`DashboardGenerationPipeline.run()` still raised:

```
ValueError: invalid dashboard spec: ['metric "aov" is kind=ratio; a bar_chart component needs a
BASE-kind metric or bare measure to group by a dimension (RATIO/GROWTH/HAVING_RATIO grouping is
not implemented in POC 2 -- see class_diagram.md)']
```

Same shape of bug as before, different rule: `DashboardValidator` has always rejected grouping by
a non-BASE-kind metric (a real POC 2 closure bug — `aov`, the `*_growth` metrics, and
`repeat_purchase_rate` all silently produced wrong-shaped results if grouped, before that
validator check existed), but the planner's prompt never told it this constraint — it only listed
each metric's kind (e.g. `aov (ratio)`) without saying which kinds are safe to group by. The
planner reasonably picked `aov` for a `bar_chart`, and lost a real LLM call to a rejection it had
no way to avoid. **Fixed**: `PlannerContext` gained a `groupable_metrics` field (mirroring
`supported_component_types`'s existing precomputed-allow-list pattern), computed in
`_build_planner_context` as every metric of kind `measure` or `base`; the prompt now states this
list explicitly and says non-groupable metrics may only be used in a `kpi` component. Two
regression tests added (`test_prompt_restricts_grouping_components_to_groupable_metrics_only` in
`tests/test_dashboard_planner.py`, `test_planner_context_never_offers_a_ratio_metric_as_groupable`
in `tests/test_pipeline_integration.py`) — full suite is now 37 passing tests.

Verified against this real dataset after the fix: the groupable-metrics list correctly comes back
as `revenue, orders, customers, orders_per_customer` — `aov`, `revenue_growth`, `order_growth`,
`customer_growth`, and `repeat_purchase_rate` are all excluded, matching what
`DashboardValidator` would accept.

## Third real bug found on the re-run (fixed) — this one was an uncaught crash, not a clean rejection

After the second fix, the real Groq call succeeded again, got past validation entirely this time,
and crashed during resolution instead:

```
ValueError: GROWTH compilation requires query.growth (AnalyticalQuery already enforces this)
```
(raised from `SQLCompiler._compile_growth`, several frames below `DashboardDataResolver.resolve`)

Worse than the first two bugs: this one wasn't caught by `DashboardValidator` at all — it reached
POC 2's engine and only failed because `AnalyticalQuery`'s own defensive check happened to catch
it. Root cause: `DashboardDataResolver.build_query()` only ever emits `AGGREGATE` or `GROUP_BY`
queries — never `GROWTH`, since a `GROWTH` query needs an explicit comparison-period pair (a
`GrowthSpec`), a decision nothing in `ComponentSpec`/`DashboardDataResolver` can make for an
arbitrary planner-chosen component (`SignalGatherer` makes that same decision, but only for its
own fixed, pre-planning diagnostic signals — see its `reference_date`). The planner picked
`revenue_growth` (kind=growth) for a **KPI** component — `DashboardValidator`'s existing metric-kind
check (`_validate_group_by_supported_for_metric`) only applied to grouping component types, so a
plain KPI with a growth metric sailed straight through to a crash.

**Fixed** two ways: (1) `DashboardValidator` gained `_validate_metric_directly_resolvable`,
applied to every component regardless of type — a growth-kind metric is now cleanly rejected with
code `growth_metric_not_directly_resolvable` instead of reaching the engine at all; (2)
`PlannerContext` gained `resolvable_metrics` (every metric except growth-kind), stated explicitly
in the prompt, so the planner is told up front that growth metrics can never be a component's
`metric_name` — they're informational context only, meant to be referenced by their pre-computed
signal value in a component's rationale text, not selected directly. Three more regression tests
(`test_growth_metric_is_rejected_as_not_directly_resolvable_even_as_a_kpi` in
`tests/test_dashboard_validator.py`, `test_prompt_never_offers_a_growth_metric_as_a_usable_metric_name`
in `tests/test_dashboard_planner.py`, `test_planner_context_never_offers_a_growth_metric_as_a_usable_metric_name`
in `tests/test_pipeline_integration.py`) — full suite is now 40 passing tests.

Verified against this real dataset after the fix: `resolvable_metrics` correctly comes back as
`revenue, orders, customers, orders_per_customer, aov, repeat_purchase_rate` — the four growth
metrics (`revenue_growth`, `order_growth`, `customer_growth`) are excluded, and the groupable
subset within it is unchanged (`revenue, orders, customers, orders_per_customer`).

## Fourth run: succeeded, but produced a silently wrong number (now fixed)

The fourth run got all the way through — a real Groq call, a validator-approved spec, and a full
`HydratedDashboard` with no crash and no rejection. Three of its four components are correct
(`total_revenue_kpi=729.39`, `total_orders_kpi=8.0`, `total_customers_kpi=8.0`,
`orders_by_price_pie` — all 7 groups correct and summing to 8). The fourth, `revenue_by_price_bar`
(grouping `revenue` by `price`), is silently wrong: its 7 group values sum to **1,556.97**, more
than double the real total of 729.39.

**Root cause**: `revenue`'s measure lives on `payments` (one row per order); grouping it by
`price` requires joining to `order_items` (one or more rows per order). That join fans out —
an order with 3 line items contributes its payment value to the `SUM` three times, once per
matching `order_items` row. Verified by hand: every group's value equals `(that order's item
count) × (that order's payment_value)` exactly. `COUNT(DISTINCT ...)`-based measures (like
`orders_by_price_pie`) are immune to this, which is exactly why that component came out right
while the `SUM`-based one didn't.

This is documented in full in `docs/real_world_integration_test.md`, including the fix that was
applied. It was a bug in shared `SQLCompiler` code — not local to this example or even to POC 3,
since POC 2 vendors the same file — so the fix was applied to both POCs at once (the user's
explicit choice); see that doc's "Finding 4" section for the full before/after, including
re-verification against this exact dataset (`revenue_by_price_bar` now sums to exactly 729.39).

## Status

Four real Groq runs so far: three wasted-call bugs found and fixed (closing off ways the planner
could pick something POC 2's engine can't resolve — see above), and a fourth that succeeded but
surfaced a real, more serious correctness bug (silently wrong numbers, not a crash or rejection —
see "Fourth run" above) — now also fixed and re-verified against this dataset.
`docs/real_world_integration_test.md` for this POC is now written up, covering all four runs.
