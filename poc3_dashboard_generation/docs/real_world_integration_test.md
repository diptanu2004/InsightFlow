# Real-world integration test: real Groq call, real Olist data, no synthetic data on either side

**What this is.** A full application-level test of `DashboardGenerationPipeline` against the two
things POC 3 had never actually been run against together before: a real Groq LLM call (no
`GROQ_API_KEY` exists in the cloud sandbox this POC was built in, so this could only run on the
user's own machine) and POC 1's real, previously-produced output for a real e-commerce dataset
(not POC 3's own hand-built sample data). Same spirit as
`poc2_analytics_engine/docs/real_world_integration_test.md`, one layer up the stack: that test
proved the POC1→POC2 handoff; this one proves the POC1/POC2→POC3 handoff, plus the one thing
neither of those could touch — a live LLM in the loop.

## Dataset and registry

Same small (8-order), real, unmodified Olist subset `poc2_analytics_engine`'s own real-data test
uses — copied into `examples/real_olist_integration/data/` for this POC's own self-containment
(see that folder's README). Registry configuration (`bootstrap_real_business_registry()` in
`run_real_test.py`) is copied unchanged from POC 2's equivalent — same real judgment calls
(`revenue` on `payments.payment_value`, not the lower-confidence `order_items.freight_value`;
`customers` inherits POC 1's per-order-surrogate vs. `customer_unique_id` vocabulary gap).
`SignalGatherer`'s `reference_date` is pinned to this dataset's own latest order date
(2018-08-03) rather than wall-clock "today" — see that README for why.

## Four real Groq runs, four different outcomes

Unlike POC 2's real-data test (which needed no live LLM and found its bugs in one sitting), this
test ran the *same* real Groq call four times, because each of the first three runs surfaced a
new real bug in code that sits between the planner and POC 2's engine — never in the Groq call
itself, which returned a structurally valid `DashboardSpec` all four times. Full detail,
reproduction, and fix for each is in `examples/real_olist_integration/README.md`; summarized here:

| # | What the planner picked | What happened | Root cause | Fix |
|---|---|---|---|---|
| 1 | `category`/`region` as a GROUP BY dimension | `DashboardValidator` cleanly rejected the whole spec (ambiguous — each maps to 2 entities in real Olist data) | `_build_planner_context` offered every canonical field name as a dimension, ambiguous ones included | Filter `available_dimensions` through `FieldResolver.find_entity_for_field` before offering them |
| 2 | `aov` (kind=ratio) for a `bar_chart` | Cleanly rejected (`group_by_unsupported_for_metric_kind` — a real, pre-existing POC 2 closure bug) | Prompt listed each metric's kind but never said which kinds are groupable | Added `PlannerContext.groupable_metrics`, stated explicitly in the prompt |
| 3 | `revenue_growth` (kind=growth) for a **KPI** | **Crashed** — `DashboardValidator` had no check for this at all; reached `SQLCompiler._compile_growth` and failed on `query.growth is None` | `DashboardDataResolver` never builds a `GROWTH` query (needs an explicit comparison-period pair nothing in `ComponentSpec` supplies); the only existing metric-kind check covered grouping components only | Added `DashboardValidator._validate_metric_directly_resolvable` (all component types) + `PlannerContext.resolvable_metrics` |
| 4 | `revenue` grouped by `price` (a `bar_chart`) | **Ran successfully — but computed a wrong number.** See below. | Fixed — see "Finding 4" | De-duplicate on a synthetic per-row id + dimension value before aggregating, applied to every cross-entity `GROUP BY` in `SQLCompiler` |

Runs 1-3 are wasted-LLM-call bugs already fixed and regression-tested. Run 4 was more serious: no
rejection, no crash — a `HydratedDashboard` was produced and it looked entirely plausible. It's
now fixed too (POC 3's suite is 42 tests).

## The real, successful output (run 4)

```json
{
  "title": "E-commerce Performance Dashboard",
  "components": [
    {"component_id": "total_revenue_kpi", "metric_name": "revenue", "value": 729.39},
    {"component_id": "total_orders_kpi", "metric_name": "orders", "value": 8.0},
    {"component_id": "total_customers_kpi", "metric_name": "customers", "value": 8.0},
    {"component_id": "revenue_by_price_bar", "metric_name": "revenue", "dimension": "price",
     "rows": [{"price": 24.89, "value": 382.68}, {"price": 78.0, "value": 347.6},
              {"price": 21.33, "value": 327.87}, {"price": 49.9, "value": 319.03},
              {"price": 18.99, "value": 107.08}, {"price": 35.0, "value": 50.35},
              {"price": 14.49, "value": 22.36}]},
    {"component_id": "orders_by_price_pie", "metric_name": "orders", "dimension": "price",
     "rows": [{"price": 49.9, "value": 2}, {"price": 35.0, "value": 1}, {"price": 21.33, "value": 1},
              {"price": 78.0, "value": 1}, {"price": 18.99, "value": 1}, {"price": 24.89, "value": 1},
              {"price": 14.49, "value": 1}]}
  ]
}
```

(trimmed to the fields that matter here — full output is what the user's terminal printed)

Three of the four components are exactly right: `total_revenue_kpi` (729.39, matches
`SUM(payments.payment_value)` over all 8 orders exactly), `total_orders_kpi`/`total_customers_kpi`
(8.0 each, correct — see POC 2's own real-data doc for the surrogate-key caveat on `customers`),
and `orders_by_price_pie` (`COUNT(DISTINCT order_id)` per price — every group is correct, and the
7 groups sum to 8, matching total orders exactly, because `COUNT(DISTINCT ...)` is immune to what
finding 4 exposes). `revenue_by_price_bar` is silently wrong, by a lot.

## Finding 4 (fixed): `GROUP BY` on a dimension reached via a one-to-many join fans out `SUM`

**The bug.** `revenue_by_price_bar`'s 7 group values sum to **1,556.97** — more than double the
actual total revenue of **729.39**. Reproduced independently with hand-written DuckDB SQL directly
against the raw CSVs (bypassing the pipeline, same discipline POC 2's own doc uses):

```sql
SELECT order_items.price, SUM(payments.payment_value) AS value
FROM payments JOIN order_items ON payments.order_id = order_items.order_id
GROUP BY order_items.price
```

Every one of the 8 orders in this dataset has one or more `order_items` rows, all sharing that
order's single price point (this dataset has no multi-price orders). `payments` has exactly one
row per order. The `JOIN` therefore produces one row per `order_items` row, not per order — an
order with 3 line items contributes its full payment value **three times** to the `SUM`. Confirmed
exactly: every one of the 7 group totals equals `(that order's item count) × (that order's
payment_value)` — e.g. `price=24.89` is the order with 3 items at $127.56 → `3 × 127.56 = 382.68`,
matching the output digit for digit.

**Why POC 2's own tests never caught this.** `poc2_analytics_engine`'s
`docs/class_diagram.md` *already* names this exact risk — but frames it as a **multi-hop** join
concern ("joins through a one-to-many relationship... can silently multiply `SUM(revenue)`"), used
as the reasoning to keep `FieldResolver.resolve_join_path` single-hop. This test shows that
framing was incomplete: `payments → order_items` is a **single hop**, well inside what POC 2's
docs call safe, and it still fans out `SUM`. The single-hop restriction prevents one real problem
(traversing an unbounded join graph) but doesn't prevent this one — what actually matters is
**join cardinality** (is the dimension's entity on the "one" side or the "many" side relative to
the measure's entity?), which `Relationship` has no way to express today, and which every existing
fixture/sample dataset happens to avoid: POC 3's own sample dataset models one product per order
directly on the `orders` row (no separate line-item table), so grouping `revenue` by `category`
there joins `orders → products`, which is many-to-one from `orders`' side — safe, by accident of
how the sample data happens to be shaped, not because the engine checks anything.

**A verified candidate fix, not yet applied.** De-duplicating on `(join key, dimension value)`
before aggregating reconciles exactly to the true total for this dataset:

```sql
SELECT price, SUM(payment_value) AS value FROM (
  SELECT DISTINCT payments.order_id, order_items.price, payments.payment_value
  FROM payments JOIN order_items ON payments.order_id = order_items.order_id
) t GROUP BY price
-- sums to 729.39, matching SUM(payments.payment_value) exactly
```

This isn't a complete fix, and is documented as such rather than oversold: an order whose items
span *multiple different* prices still has its full payment value counted once per distinct price
it touches, so cross-group sums still don't perfectly reconcile to the grand total in that
specific case (a real, defensible "multi-attribution" ambiguity, the same kind a revenue-by-category
split faces for an order spanning categories — not the same failure as the original bug, which
duplicated blindly by row count with no attribution logic at all). It also only applies where
`SUM`/`AVG`/plain `COUNT` are fan-out-vulnerable — `COUNT(DISTINCT ...)` already isn't, which is
exactly why `orders_by_price_pie` came out correct above with no fix needed.

**Applied.** This changes `SQLCompiler._compile_base`'s cross-entity `GROUP BY` path — shared,
previously "closed" code exercised by every grouping metric in both POC 2 and POC 3, not a
POC-3-local file — so it was applied to both POCs at once (per the user's explicit choice: "fix it
now in both POCs"), not just here. The actual implementation (`_compile_base` split into
`_compile_base` + `_compile_grouped_base` in `src/insightflow/compilation/sql_compiler.py`) differs
slightly from the candidate fix sketched above: rather than de-duplicating on `(join key,
dimension value)`, every row of the measure's entity is given a synthetic, guaranteed-unique id
(`ROW_NUMBER() OVER ()`) *before* the join, and de-duplication happens on `(row id, dimension
value)` instead. The join-key column itself turned out not to be safe to use directly — it's only
unique per row by accident of this particular dataset's shape (`payments.order_id` happens to be
unique because this dataset has one payment row per order); in general (e.g. `orders`'s own
`product_id`/`customer_id` FK columns, which repeat across many `orders` rows even though *that*
join direction doesn't fan out at all) it isn't, and using it directly risked coincidentally
merging two genuinely different rows that happened to share a join-key value and a raw measure
value. A synthetic row id sidesteps that risk entirely and generalizes correctly regardless of
which side of the relationship `measure.entity` sits on.

Regression test `tests/test_join_fanout_fix.py` (a `payments`/`order_items`-shaped fixture matching
this exact repro) locks in both the fan-out fix and the documented multi-attribution behavior, and
fails against the pre-fix code (asserts a 180.0 grouped total; the old code gives 410.0). Re-run
end to end against this real dataset after the fix: `revenue_by_price_bar`'s 7 groups now sum to
exactly **729.39**, matching `total_revenue_kpi` and the true `SUM(payments.payment_value)`.
`test_category_performance_group_by`/`test_regional_performance_group_by_across_join` (POC 2's own
sample-dataset tests, which join in the safe direction) continue to pass with byte-identical
results, confirming the fix is a no-op when the join doesn't actually fan out. Ported identically
into `poc2_analytics_engine`'s original copy of `sql_compiler.py` in the same change — see that
POC's own `docs/real_world_integration_test.md` ("Finding 8") and `docs/class_diagram.md`
(closure-notes item 13) for its side of this writeup.

## The full-scale run (~99.4k orders) — `examples/real_olist_full_scale/`

Run by the user on their own machine (`run_full_scale_test.py`, no data/API key in the cloud
sandbox), against the real registry/mapping above at real row counts instead of the 8-order
subset. Reference date discovered dynamically as `MAX(orders.transaction_date)` = 2018-10-17.
Pipeline build (7 CSVs registered as DuckDB views): 468ms. Full dashboard generation (signals +
one real Groq call + validation + resolution): 5.7s. A validator-approved 5-component
`HydratedDashboard` came back on the first try — no crash, no rejection, at ~12,500x the row count
of every prior real-data run:

```
total_revenue_kpi      = 16,008,872.119998764   (matches poc2_analytics_engine's own full-scale
                                                  reference number exactly: 16,008,872.12)
total_orders_kpi       = 99,441.0                (exact match)
repeat_purchase_kpi    = 0.0                     -- see Finding 5 below
revenue_by_price_bar   grouped by "price" (top 20 of 20+ distinct values)
orders_by_price_pie    grouped by "price" (same dimension, same reason -- see below)
```

`revenue`/`orders` reconciling exactly to the reference numbers at this scale is the main thing
this test existed to prove: the vendored engine (join-fan-out fix included) and
`DashboardValidator` hold up at real row counts, not just a handful.

**Why both grouped components used `price`, not `category`/`region`.** Not a planner miss: both
are ambiguous in this real semantic model (`category` maps to `products` AND
`category_translation`; `region` maps to `customers` AND `sellers`), exactly Finding 1's mechanism
from the small-scale run — `_build_planner_context` filters ambiguous dimensions out before the
planner ever sees them, so `price` was the only unambiguous groupable dimension available. Minor
chart-selection note, not a bug: a pie chart over 20+ near-even `price` slices (LIMIT-20 truncated)
communicates less clearly than a bar chart would for the same data — worth a planner-prompt nudge
("prefer bar over pie once a dimension has many distinct values") if this recurs, not fixed here.

## Finding 5 (fixed): `repeat_purchase_rate` silently computes 0.0 against real Olist data

**The bug.** `repeat_purchase_kpi` came back exactly **0.0** at full scale — not implausibly low,
*exactly* zero, which is what made it worth tracing rather than accepting as "retention is bad."

**Root cause.** `HAVING_RATIO`'s `group_by_field` resolved, by default, in the SAME entity as its
measures (`orders`) — but real Olist `orders` rows carry only a per-ORDER surrogate `customer_id`
(every order gets its own, even for a returning customer); the real per-PERSON identity,
`customer_unique_id`, lives only on the `customers` entity, one join away. Worse: even
`customers` doesn't expose it under its own name — POC 1's real mapping collapses BOTH
`customer_id` and `customer_unique_id` onto the one canonical name "customer_id" there too (see
`FieldResolver`'s docstring, "second implementation note"), so the existing highest-confidence
tie-break can't reach it even with a join. Grouping by a column that's unique to every order makes
every group size exactly 1, so "≥2 orders in a group" is structurally impossible — a
mechanically-correct answer to the wrong grouping key, not an arithmetic bug, and the reason it
never triggered a validator rejection or a crash.

**Why the earlier (8-order) real-data run never caught this.** `repeat_purchase_rate` was never
picked by the planner in any of the four small-scale runs — the first real exercise of this
metric's grouping logic against real data was this full-scale run.

**Fixed.** Added two new optional `MetricDefinition` fields, `group_by_entity` and
`group_by_source_column` (both `None` by default — a no-op for every existing HAVING_RATIO metric
whose grouping field already lives in its own measures' entity with no name collision).
`group_by_entity="customers"` tells `_compile_having_ratio` to join to a different entity for the
grouping key (safe by construction here, not the Finding-4 risk: `orders` — the base of every
aggregate in this query — holds the FK, so each `orders` row matches AT MOST ONE `customers` row;
the join can only narrow rows, never fan them out, regardless of aggregation type).
`group_by_source_column="customer_unique_id"` bypasses `resolve_field`'s highest-confidence
tie-break with a new `FieldResolver.resolve_field_by_source_column`, reaching the specific physical
column directly. The ratio's denominator also had to change for the cross-entity case: the
registry's `denominator_measure` still counts `orders`' own surrogate key, which is exactly the
wrong-population problem this fix solves, not a different one — so when `group_by_entity != entity`,
the denominator is now `COUNT(*) FROM grp` (the grouping CTE itself, already grouped on the real
key) instead of a separately-configured measure query. This is a no-op for the existing
same-entity path (there, `COUNT(*) FROM grp` and the configured `denominator_measure` count the
same thing by construction) and correct for the new cross-entity one.

Applied to both `poc2_analytics_engine`'s original `sql_compiler.py`/`field_resolver.py`/
`registry.py` and this POC's vendored copies in the same change (shared code, same convention as
Finding 4) — see that POC's own `docs/real_world_integration_test.md` ("Finding 9") for its side.
Both `examples/real_olist_integration/run_real_test.py` and
`examples/real_olist_full_scale/run_full_scale_test.py` (in both POCs) now set
`group_by_entity="customers"` / `group_by_source_column="customer_unique_id"` on
`repeat_purchase_rate`.

**Regression tests** (`tests/test_having_ratio_cross_entity.py`, identical in both POCs): a small
synthetic fixture reproducing the exact real-world shape (5 orders, each its own never-repeating
surrogate `customer_id`, joining to a `customers` entity where the real `customer_unique_id`
repeats for 2 of 3 real customers) — unfixed config locks in the bug's exact symptom
(`repeat_purchase_rate == 0.0` even though real repeat customers exist); fixed config gets the
correct `2/3`; a third test locks in that the existing same-entity path stays byte-identical.

**Re-verified against real data, at both scales.** Re-run against the small 8-order subset after
the fix: the compiled SQL now correctly joins `orders` to `customers` and groups by
`customer_unique_id` — the result is still `0.0`, but for the right reason this time: all 8 orders
in that particular subset genuinely belong to 8 distinct real people (confirmed by inspecting
`customers.csv` directly), not because the grouping key can never see a repeat. **Re-run at full
scale next, and confirmed**: `repeat_purchase_kpi` now comes back **0.031187562437562436** (≈3.1%)
— a real, plausible number (independently consistent with widely-reported repeat-purchase rates
for the real Olist dataset, which is known to be very low by `customer_unique_id`), not the
previous structurally-guaranteed 0.0. Same run's `total_revenue_kpi` still matched the reference
number exactly (16,008,872.12), confirming the fix didn't disturb anything else. The dashboard's
`narrative` for this run — "Revenue remains sizable, but both orders and customers have fallen
50%, and the repeat purchase rate is low, indicating the business relies heavily on acquiring new
customers and faces sustainability risk" — is also the first real evidence the planner-prompt
reconciliation instruction (added for the retention-problem benchmark's usefulness gap) does what
it's meant to: it states a verdict connecting the decline signals and the repeat-rate KPI, not just
a list of what's on the dashboard.

Minor, non-blocking chart-selection note from this same run: 3 of its 5 components
(`orders_by_price_bar`, `revenue_by_price_pie`, `customers_by_price_table`) all group by the same
single dimension (`price`), just with different metrics/chart types — not wrong (still the only
unambiguous groupable dimension in this real mapping), but less varied than the earlier full-scale
run's 2-of-5. Not acted on here; worth a planner-prompt nudge toward dimension variety if it
recurs once a second unambiguous real dimension exists to compare against.

## What this test does and doesn't prove

Proves: the real Groq call, structured-output parsing, and every downstream stage — including,
now that finding 4 is fixed, cross-entity `GROUP BY` aggregation — work end to end against real
data at BOTH an 8-order and a ~99.4k-order scale, and that grouped-chart numbers reconcile exactly
to their ungrouped totals when the underlying join doesn't fan out. Proves the "tell the planner
everything the validator will enforce" pattern (findings 1-3) closes the gap between "the LLM is
creative" and "the LLM never wastes a call on something that will be rejected." Finding 5 (this
doc) additionally proves that "mechanically valid" and "actually correct" aren't the same
guarantee — `repeat_purchase_rate` was validator-approved and numerically wrong at the same time,
and only real row counts against a real, imperfect POC1 mapping surfaced it.

Still not proven in general: a grouped chart whose dimension's entity has rows spanning more than
one distinct dimension value per parent row (the documented multi-attribution residual limitation)
hasn't been exercised against real data, only against the synthetic fixture in
`tests/test_join_fanout_fix.py`. `SignalGatherer`'s try/except degradation was not exercised under
real load either (no signal failed at either real-data scale run so far). `repeat_purchase_rate`'s
Finding-5 fix is now proven correct by regression tests AND a real non-zero full-scale number
(≈3.1%, plausible against known public figures for this dataset) — the only piece missing at this
doc's previous revision.

Also evaluated separately from this doc (real-Groq-call runs against the two synthetic benchmark
semantic models `hld.md`'s evaluation section calls for, see `examples/benchmark_declining_revenue/`
and `examples/benchmark_retention_problem/`): the retention-problem case was mechanically flawless
on its first run (every number matched `expected_values.json` exactly) but exposed a real
usefulness gap — the planner queried `repeat_purchase_rate` correctly but never connected it to the
healthy-looking growth signals in its narrative. `DashboardPlanner`'s prompt was fixed to instruct
it to name that kind of tension rather than describe conflicting signals as independently good news
(see `planner.py` and `tests/test_dashboard_planner.py`'s
`test_prompt_instructs_planner_to_reconcile_conflicting_signals_in_the_narrative`), and then
**confirmed on a second real Groq run of this exact case**: the new narrative reads "Revenue,
orders, and customers are all up around 40%, but the low repeat purchase rate shows growth is
driven by new customers rather than repeat business" — a direct statement of the tension this
benchmark case exists to test, not a generic growth summary. Full write-up in
`examples/benchmark_retention_problem/README.md`'s own "Results" section.

The declining-revenue case has also now been run and evaluated (one real Groq run): every number
matched `expected_values.json` exactly, absence-of-unsupported-metrics and chart selection both
passed, and the narrative correctly identified and quantified the decline with a real,
signal-grounded reconciliation (declining growth vs. a healthy repeat-purchase rate — the same
reconciliation behavior the retention-problem fix produces) — but it did not call out Electronics
specifically as the disproportionate driver, which is the one thing this benchmark's shape was
built to test for. That gap was investigated and found to be structural, not a planner/prompt
issue: the Dashboard Spec DSL has no field anywhere (`ComponentSpec.time_grain` only applies to the
not-yet-buildable `LINE_CHART`; `FilterSpec` carries only `{dimension, label}`, no value/range, and
`DashboardDataResolver` never applies it to a query) that could scope `revenue_by_category` to a
recent time window, so a category breakdown can only ever be a whole-period snapshot — which
necessarily can't reveal a share-shift-over-time story like Electronics' 45%→10% decline. Same root
cause as the already-documented "no time-bucketing primitive in POC 2's AST" limitation
(`docs/class_diagram.md`), not a new bug. Full write-up in
`examples/benchmark_declining_revenue/README.md`'s own "Results" section.
