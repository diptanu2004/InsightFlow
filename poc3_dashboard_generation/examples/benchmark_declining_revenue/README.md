# Benchmark case: declining revenue

One of the two benchmark semantic models `docs/hld.md` flagged as an open question ("A
declining-revenue case and a retention-problem case need to be constructed or sourced before the
evaluation step ... can run") -- POC 2's real Olist data (`examples/real_olist_integration/`,
`examples/real_olist_full_scale/`) only ever covered one "healthy" case. See
`examples/benchmark_retention_problem/` for the other one.

**Synthetic, not real data, on purpose.** The point here isn't testing against real-world
messiness (that's what the Olist examples are for) -- it's giving `DashboardPlanner` a business
scenario with a known, deliberately-engineered shape, so its output can be judged against a
rubric instead of by "does it look plausible."

## The shape

`generate_data.py` builds 240 days of orders (see its own module docstring for full detail):
daily order volume declines steadily (~9/day -> ~2/day), and the Electronics category's share of
that (already-shrinking) volume shrinks even faster (45% -> 10%) -- one product line going stale,
not a uniform slowdown. Every growth signal `SignalGatherer` computes (the last 90 days vs. the
preceding 90) comes out clearly and consistently negative:

```
revenue_growth   ≈ -0.38
order_growth     ≈ -0.44
customer_growth  ≈ -0.31
repeat_purchase_rate ≈ 0.56   (deliberately kept unremarkable -- not the story here)
```

(Exact numbers, plus per-category revenue, are in `data/expected_values.json`, computed
independently in plain Python at generation time -- not by running the engine and trusting it.)

## Regenerate the data

```
python3 generate_data.py
```

Deterministic (fixed seed) -- re-running reproduces byte-identical CSVs and
`expected_values.json`. `tests/test_benchmark_datasets.py` (run from the POC 3 root) verifies the
real engine's numbers against this file exactly, and that `SignalGatherer` actually surfaces the
decline before any LLM is involved.

## Run the real benchmark (real Groq call, your machine only)

```
export GROQ_API_KEY=...     # or rely on the .env file already in poc3_dashboard_generation/
uv run python examples/benchmark_declining_revenue/run_benchmark_test.py
```

No `GROQ_API_KEY` exists in the cloud sandbox this POC was built in, so -- like every other
real-Groq-call script in this project -- this has to be run on your own machine; paste the output
back for evaluation.

## Evaluation rubric

Per `docs/hld.md`'s evaluation step ("usefulness, chart selection, absence of unsupported metrics
(mechanically checked)"):

- **Absence of unsupported metrics** is already guaranteed mechanically -- `DashboardValidator`
  can't let an unsupported spec through `pipeline.run()` at all. Not a judgment call.
- **Every number matches `data/expected_values.json`.** Any KPI or growth component that
  disagrees with it is a real bug (in `SQLCompiler`, `DashboardDataResolver`, or the prompt), not
  a planner judgment call -- check this first.
- **Usefulness**: does the dashboard's `narrative`/component `rationale`s actually say the
  business is declining, and point at *why* (Electronics specifically) rather than a generic
  "revenue is X" readout? A dashboard that reports the numbers without noticing the trend is a
  planner-prompt gap, not a validator gap.
- **Chart selection**: does it include a `revenue`-by-`category` component (the one grouping that
  would show Electronics' disproportionate decline), rather than only ungrouped KPIs? Given only
  `region`/`category` as real dimensions, a chart-worthy dashboard for a declining-revenue case
  should reach for the one that explains *why*.

## Results

**Run once, on the user's machine, real Groq call.** Every number matched
`data/expected_values.json` exactly: `total_revenue_kpi` = 93,963.08, `total_orders_kpi` = 1,327,
`repeat_purchase_kpi` = 0.5631067961165048 (≈0.5631), and `revenue_by_category` digit-for-digit
against `category_revenue` (Home 28,644.36, Apparel 28,381.00, Electronics 21,153.19, Grocery
15,784.53). **Absence-of-unsupported-metrics passes** (nothing rejected). **Chart selection
passes**: the dashboard included `revenue_by_category` (bar chart) alongside `orders_by_region`
(pie chart) and `customers_by_category` (table) -- five components total, reasonable variety.

**Usefulness: partial pass.** The narrative correctly identifies and quantifies the decline with a
real, signal-grounded explanation -- *"Revenue, orders, and customer counts have all declined
sharply (-38%, -44%, -31%), indicating deteriorating business health, but the repeat purchase rate
remains relatively strong, suggesting the core customer base is still engaged while the decline is
driven by loss of new customers."* That's a genuine, non-generic reading of the signals (it
correctly reconciles the healthy repeat-purchase rate against the negative growth numbers, the same
reconciliation behavior the `benchmark_retention_problem` fix was built to produce). It does **not**,
however, call out Electronics specifically, which is the one thing this benchmark's shape was
designed to test for.

Investigated whether this is a fixable planner/prompt gap or a structural one, and it's structural:
`revenue_by_category` is necessarily an ungrouped-by-time snapshot over the *entire* 240-day window,
because nothing in the Dashboard Spec DSL can scope a `BAR_CHART`/`PIE_CHART`/`TABLE` component to a
recent time window. `ComponentSpec.time_grain` (`src/insightflow/dashboard/spec.py`) is only
meaningful for `LINE_CHART`, which is modeled but not resolvable in POC 3 v1 (rejected by
`DashboardValidator` outright). `FilterSpec` (the DSL's only other filter-shaped field) carries just
`{dimension, label}` -- no value, no range, no operator -- and `DashboardValidator._validate_filters`
only checks that the named dimension resolves to a real field; `DashboardDataResolver` never
consults `spec.filters` when building queries (`resolver.py` just passes them through unchanged into
`HydratedDashboard` for the frontend to render as filter chips). So there is no field anywhere in the
DSL a planner could use to ask for "revenue by category, last 90 days only" -- the query that would
actually surface Electronics' 45%->10% share collapse. Over the full 240-day window, Electronics
lands 3rd of 4 categories (21,153.19) rather than obviously worst, because the snapshot averages the
early period (when Electronics still had a large share) together with the recent period (when it
didn't) -- a category snapshot alone genuinely can't reveal a share-shift-over-time story.

This is the same underlying gap already on record in `docs/class_diagram.md` (no time-bucketing
primitive in POC 2's AST; GROUP_BY only works on BASE-kind metrics, never GROWTH-kind; `LINE_CHART`
is modeled but not buildable) -- not a new bug, and not something a prompt tweak can fix, since the
DSL itself has no field to carry a time-windowed group-by request. Fixing it for real means adding a
time-window/date-range predicate to `ComponentSpec` (or giving `FilterSpec` an actual value + having
`DashboardDataResolver` apply it), which is an AST/DSL-level change belonging with the other
"structural, not yet fixed" items rather than a same-session patch.

**Verdict on this case**: 2 of 3 rubric checks pass cleanly (numbers, absence of unsupported
metrics) plus chart selection; usefulness is a partial pass -- correct and specific about the
*decline*, generic about the *cause*, for a structural reason now documented rather than guessed at.
