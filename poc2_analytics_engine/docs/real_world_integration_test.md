# Real-world integration test: POC 1 output → POC 2, no synthetic data on either side

**What this is.** A full application-level test of the actual interface between POC 1 and POC 2:
take POC 1's real, previously-produced output for a real e-commerce dataset (not the sample data
either POC ships with), configure POC 2's registry against exactly what that output says, and run
real business metrics through the real pipeline. The goal wasn't to prove the numbers are
right — it was to find out where the two POCs' assumptions about each other don't quite line up,
since neither POC has ever been run against the other's real output before.

## Dataset: a real, unmodified subset of the Olist e-commerce dataset

`poc1_schema_discovery/data/raw/eval_real_olist/` (7 CSVs: `orders`, `customers`, `order_items`,
`products`, `sellers`, `payments`, `category_translation`) is a small, referentially-consistent
subset of the real, public Brazilian e-commerce dataset by Olist — real hashed IDs, real
Portuguese product category names (`ferramentas_jardim`, `beleza_saude`, ...), real Brazilian
cities/states, real nulls (one product has no category), and the genuine `product_name_lenght`
typo baked into Olist's own source data. It was already fetched and used by POC 1's own
evaluation suite (see `poc1_schema_discovery/README.md`'s "`eval_real_olist`" section) — this
test reuses it rather than fetching a second copy, and neither the CSVs nor POC 1's
`olist_model.json` output were modified in any way to make this test easier.

**Why not a fresh, larger fetch, and why not a fresh POC 1 run.** Two hard constraints, checked
before starting rather than assumed: this session's outbound network access is allowlisted to
package registries and a few API hosts, so a direct download of a dataset archive (tried:
`archive.ics.uci.edu`) is blocked at the network layer, not just inconvenient; and POC 1's
semantic mapping step calls a Groq LLM, and no Groq API key is available in this cloud sandbox
(only present in `poc1_schema_discovery/.env` on the user's own machine, where there is currently
no way to execute Python from this session either). So a *fresh* POC 1 run — on this dataset or
any other — isn't feasible from here today, independent of which dataset is used. `olist_model.json`
is real POC 1 output nonetheless: it's what POC 1 actually produced the one time it *was* run
against this dataset, on the user's own machine, with a real key. This test is honest about using
that prior output rather than presenting it as a fresh run.

## Registering measures against real POC 1 output

`poc1_schema_discovery/olist_model.json`'s mapping is realistically imperfect — POC 1's own
README already documents several of its rough edges (see "Vocabulary is too coarse..." there).
Configuring POC 2's registry against it meant making the same judgment calls a real analyst
would:

- **`revenue`** is mapped to *two* different physical columns: `order_items.freight_value`
  (confidence 0.55 — POC 1's own README flags this as likely mislabeled shipping cost, not
  revenue) and `payments.payment_value` (confidence 0.95 — the actual amount paid). Registered
  `revenue` against `payments.payment_value`: higher confidence and the semantically correct
  choice.
- **`customers`** (via `orders.customer_id`, COUNT DISTINCT) inherits a real POC 1 vocabulary
  gap: in the real Olist schema, `customer_id` is a per-order surrogate key, while
  `customer_unique_id` is the stable identity of a returning customer — and POC 1 mapped *both*
  to the same canonical `customer_id`. Because they share one canonical name, POC 2's registry
  has no way to ask for "the stable one" — this measure counts per-order surrogates, not unique
  humans, which is a POC 1 vocabulary limitation (documented in POC 1's own README), not
  something POC 2 can route around from the registry layer.
- `aov`, `order_growth`, `customer_growth`, `repeat_purchase_rate` registered the same way as the
  sample dataset's `bootstrap_registry()`, just pointed at the real entities/measures above.

See `examples/real_olist_integration/run_real_test.py` for the exact registry configuration.

## Results

| Query | Result | Notes |
|---|---|---|
| `revenue` | **729.39** | `SUM(payments.payment_value)` over all 8 orders |
| `orders` | **8.0** | distinct `order_id` |
| `customers` | **8.0** | distinct `orders.customer_id` — see the surrogate-key caveat above; happens to equal `orders` here because none of the 8 sample orders share a customer |
| `aov` | **91.17375** | `729.39 / 8`, exact |
| `repeat_purchase_rate` | **0.0** | correctly reflects that none of the 8 sample customers placed a second order — a real result of the sample being small, not a bug |
| `order_growth` (2018 vs 2017) | **0.0** | 4 orders in each year in this sample |
| `customer_growth` (2018 vs 2017) | **0.0** | same reasoning |
| `revenue_growth` (2018 vs 2017) | **cleanly rejected** | see finding 5 below |
| revenue by `category` | **cleanly rejected** | see finding 3 below |
| revenue by `region` | **cleanly rejected** | see finding 3 below |

Every scalar number above was cross-checked by hand against the raw CSVs before trusting the
engine's output (not the other way around) — `revenue`, `orders`, `aov`, and both growth figures
all match hand sums exactly.

## Findings — five real gaps this surfaced, all fixed except where noted

None of these showed up against either POC's own sample/fixture data, because that data was
built by whoever wrote each POC's tests — including this one — and so silently matched whatever
that person assumed. Testing the actual POC1→POC2 handoff for the first time is what surfaced
them.

1. **`QueryExecutor.register_sources` assumed `source_file` included the file extension.**
   POC 1's real `SemanticField.source_file` is the bare basename with no extension
   (`"orders"`, not `"orders.csv"`) — confirmed against two independent real POC 1 outputs
   (this Olist run and the earlier non-Olist sample run), not just this one dataset. The
   hand-built sample `data/semantic_model.json` shipped with POC 2 happened to already include
   `.csv`, which is exactly why this bug was invisible until tested against real POC 1 output.
   **Fixed** in `src/insightflow/execution/query_executor.py` (now appends `.csv`); POC 2's own
   sample `data/semantic_model.json` was also corrected to match POC 1's real convention.
2. **`FieldResolver.resolve_field` took the first matching field, not the best one.** POC 1
   can (and did, for this dataset) map more than one physical column to the same canonical name
   within a single entity — `customer_id`/`customer_unique_id` both to `customer_id`;
   `customer_zip_code_prefix`/`customer_city`/`customer_state` all to `region`. **Fixed** to pick
   the highest-`confidence` match, in `src/insightflow/compilation/field_resolver.py`.
3. **`ASTValidator._validate_dimension` didn't check for ambiguity, only existence.** A
   dimension mapped into more than one entity (Olist's `category`: `products` *and*
   `category_translation`; `region`: `customers` *and* `sellers`) passed validation and then
   raised an unhandled `ValueError` deep inside `SQLCompiler`/`FieldResolver.find_entity_for_field`
   instead of failing cleanly at the validation stage. **Fixed** in
   `src/insightflow/validation/ast_validator.py` — both queries above now fail with a clear
   `ambiguous_dimension` `ValidationError` instead of a raw exception.
4. **`SQLCompiler.TIME_FIELD` was a made-up name (`"order_date"`) that never matched POC 1's
   real vocabulary.** POC 1's actual canonical vocabulary (`semantic/vocabulary.py`) uses
   `"transaction_date"`. The mismatch was invisible because POC 2's own hand-built sample data
   used the same made-up name. **Fixed**: `TIME_FIELD` is now `"transaction_date"`, and POC 2's
   sample `data/semantic_model.json` was corrected to use POC 1's real vocabulary names
   throughout (`transaction_date`, `customer_name`, `product_name` — POC 1 has no generic `name`
   field).
5. **`ASTValidator` didn't check that a GROWTH metric's base measure entity actually has a time
   field.** `revenue`'s measure lives on `payments`, which has no date column at all in the real
   Olist schema (dates live on `orders`) — the same class of gap as finding 3, just for GROWTH
   instead of GROUP_BY. **Fixed** in `src/insightflow/validation/ast_validator.py`:
   `revenue_growth` now fails cleanly with a `growth_entity_missing_time_field` error rather than
   an unhandled `KeyError`. **Not fixed, and deliberately not worked around**: computing
   `revenue_growth` for real would require `SQLCompiler._compile_growth` to join from `payments`
   to `orders` for the date filter (the relationship exists in the semantic model), which
   `_compile_growth` doesn't support today — `_compile_base`/`_compile_ratio` do, `_compile_growth`
   doesn't. Left as a real, scoped gap rather than patched under time pressure, consistent with
   POC 1's own "don't fix reactively mid-POC" practice (see its README's "Roadmap" section).

## Part 2: the same test at real-world scale

**What changed.** The section above ran against an 8-order referentially-consistent subset. The
user separately downloaded the actual full **Brazilian E-Commerce Public Dataset by Olist** from
Kaggle (~99.4k real orders, 9 CSVs) directly onto their own machine — genuinely unmodified,
genuinely full scale, not a synthetic stand-in. Getting the files into this environment wasn't
trivial either: the device bridge's file-transfer tool failed repeatedly on anything over ~2MB
(reproducible "upload failed" / "wall-clock timeout" errors across five attempts, on a link that
had handled the small dataset fine), so the user attached the 7 needed CSVs to the chat directly
instead, which worked. Two files in the download (`geolocation`, `order_reviews`) aren't used by
any of the ten target metrics and were skipped.

**Same semantic mapping, deliberately.** `examples/real_olist_full_scale/` reuses
`examples/real_olist_integration/data/semantic_model.json` — POC 1's *real* mapping — unchanged.
That's not a shortcut: POC 1's mapping is a column-name → canonical-field mapping (schema-level),
and the full download's CSV headers were confirmed byte-identical to the small sample's before
writing the test. What changed is only row counts (8 → 99,441 orders) and, as it turned out, data
completeness — the small sample happened to be perfectly referentially consistent (every order
has exactly one payment record); the full dataset, being real, isn't.

**Results** (`examples/real_olist_full_scale/run_full_scale_test.py`; pipeline build — registering
all 7 CSVs as DuckDB views — took ~250-430ms, each query 40ms-900ms):

| Query | Result | Notes |
|---|---|---|
| `revenue` | **16,008,872.12** | `SUM(payments.payment_value)`, all 103,886 payment rows |
| `orders` | **99,441.0** | distinct `order_id` — matches `orders.csv` row count exactly |
| `customers` | **99,441.0** | distinct `orders.customer_id` — the surrogate-key caveat from finding 2 above, now visible with a real number: the *true* unique-human count (`customer_unique_id`, unreachable from the canonical layer) is **96,096** — 3,345 real repeat customers this measure cannot see |
| `aov` | **160.98864774085905** | see finding 6 — this number is only correct after that fix |
| `repeat_purchase_rate` | **0.0** | `MAX(orders per surrogate customer_id)` in the real data is 1 — a direct, real-scale confirmation of the finding-2 caveat: every "customer" by this measure's definition ordered exactly once, because it's really counting orders, not people |
| `order_growth` / `customer_growth` (2018 vs 2017) | **0.19755659519744573** (both, identically) | see finding 7 — wrong (0.19952...) before that fix; identical to each other because `customer_id` is ~1:1 with `order_id` here (same root cause as the `customers` row above) |
| `revenue_growth` (2018 vs 2017) | **cleanly rejected** | same finding-5 gap as the small-scale test — `payments` still has no date column |
| revenue by `category` / `region` | **cleanly rejected** | same finding-3 ambiguity as the small-scale test |

Every number above was independently recomputed with hand-written DuckDB SQL directly against the
raw CSVs (bypassing the pipeline entirely) before being trusted — including deliberately
reproducing what the *old*, pre-fix code would have computed, to confirm each "wrong" number
really was wrong and not a red herring.

## Findings 6 and 7 — only surfaced by real row counts, both fixed

Findings 1-5 above were about the POC1 → POC2 *interface*. These two are internal `SQLCompiler`
bugs that no amount of testing against 8 referentially-perfect rows could have caught — they
needed real scale and real data-quality gaps to become visible at all, which is the entire reason
this second test round exists.

6. **Cross-entity `RATIO` compiled a `JOIN` between the numerator and denominator entities, then
   aggregated *both* over that one joined row set.** `_compile_ratio` did
   `SELECT SUM(payments.revenue) / COUNT(DISTINCT orders.order_id) FROM payments JOIN orders ...`.
   An INNER JOIN keeps only rows that match on both sides — so any row on either side lacking a
   counterpart on the other silently vanishes from *both* aggregates simultaneously. This was
   untestable against `data/`'s sample: `bootstrap_registry()` puts `revenue` and `orders` on the
   *same* entity, so the join branch never even ran there. The real Olist registry needs `revenue`
   on `payments` and `orders` on `orders` — genuinely different entities — and the real data has
   exactly one real order (status `delivered`, not canceled) with zero matching payment rows. The
   join silently dropped it from `aov`'s denominator (99,441 → 99,440), understating the count of
   orders with no error raised: `aov` came out as **160.99026669347955** instead of the correct
   **160.98864774085905**. **Fixed**: `_compile_ratio` now computes the numerator and denominator
   as two independent scalar subqueries (the same pattern `_compile_growth` already used) whenever
   they live in different entities — no join at all, so an incomplete relationship between the two
   entities can no longer corrupt either side. Locked in by
   `tests/test_ratio_and_time_filter_fixes.py::test_cross_entity_ratio_denominator_is_not_corrupted_by_the_join`
   (a 3-orders/2-payments fixture, deliberately shaped like the real gap) — it fails against the
   old join-based code (asserts 10.0; the old code gives 15.0).
7. **Time filters used `col BETWEEN $start AND $end`, silently binding `end_date` as midnight.**
   `TimeFilter.end_date` (`models/query.py`) is a calendar `date`, not a timestamp — a caller can
   only say "through this whole day," never "through this exact instant." `BETWEEN` against a bare
   date literal binds it as `00:00:00`, so every row timestamped *later* that same day is silently
   excluded. `data/raw/sample`'s hand-picked growth periods never happened to have a row land
   exactly on a boundary, so this was invisible there too. At real scale it wasn't: 74 real orders
   placed on `2017-12-31` after midnight fell inside a `2017-01-01..2017-12-31` comparison period
   and should have counted, but didn't — `order_growth`/`customer_growth` (2018 vs 2017) came out
   as **0.19952472960668044** instead of the correct **0.19755659519744573**. **Fixed**: the
   predicate is now `col >= $start AND col < $end_exclusive`, where `$end_exclusive` is
   `end_date + 1 day` computed in Python (`src/insightflow/compilation/sql_compiler.py`,
   `_time_filter_predicate`) — portable, no DB-specific date arithmetic needed. Locked in by
   `tests/test_ratio_and_time_filter_fixes.py::test_time_filter_end_date_includes_the_whole_last_day`
   and `::test_growth_period_end_date_includes_the_whole_last_day`.

Both fixes were re-verified against *both* real-data examples afterward (`run_real_test.py` and
`run_full_scale_test.py`) and against the full 48-test suite — nothing regressed.

## Finding 8 — found one layer up, in POC 3, against this same engine

`poc3_dashboard_generation` vendors this file's `SQLCompiler` unmodified and ran it against this
same small real Olist subset through a real Groq-driven `DashboardPlanner` (its own
`docs/real_world_integration_test.md`, "Finding 4," has the full writeup). A dashboard component
grouping `revenue` (measure on `payments`, one row per order) by `price` (on `order_items`,
one-or-more rows per order) came back **1,556.97** against a true total of **729.39** — more than
double. Root cause: `_compile_base`'s cross-entity `GROUP BY` path did a plain `JOIN` + `GROUP BY`
with no cardinality awareness — an order with 3 line items contributed its full payment value to
the `SUM` three times, once per matching `order_items` row.

Neither the small-scale nor the full-scale round of this file's own testing had a fixture shaped
to expose this: every cross-entity `GROUP BY` this file's own tests ever ran (`category` and
`region` via `bootstrap_registry()`'s sample data) joins in the *safe* direction — `orders` holds
the FK, and the dimension's entity (`products`/`customers`) is the "one" side, so the join never
fans out `orders`' rows. This file's own `docs/class_diagram.md` already named the fan-out risk in
general, but framed it as a **multi-hop**-join concern, used to justify keeping
`FieldResolver.resolve_join_path` single-hop — POC 3's finding proves that framing was incomplete:
`payments → order_items` is a single hop, and it still fans out. What actually matters is **join
cardinality** (is the dimension's entity on the "one" side or the "many" side relative to the
measure's entity?), which `Relationship` has no way to express, and which this file's own sample
dataset happens to avoid entirely by construction.

**Fixed** in `_compile_base` (split into `_compile_base` + `_compile_grouped_base`): every row of
`measure.entity` is given a synthetic, guaranteed-unique id (`ROW_NUMBER() OVER ()`) before the
join, and the joined result is de-duplicated on `(row id, dimension value)` before aggregating.
A row matching several children with the *same* dimension value (the fan-out case) is counted
once; a row matching children with *different* dimension values (a genuine multi-attribution case
— one order with line items at two different prices) is counted once per distinct value it
touches — a documented, defensible residual limitation, not the same failure as the original
bug's blind row-count duplication. Applied unconditionally to every cross-entity `GROUP BY`
(there's no cardinality metadata to conditionally gate it on), so it is a no-op whenever the join
doesn't actually fan out — verified by `test_category_performance_group_by` and
`test_regional_performance_group_by_across_join` continuing to pass with byte-identical results.
Locked in by `tests/test_join_fanout_fix.py` (a `payments`/`order_items`-shaped fixture, the same
shape as the real repro) — fails against the old code (asserts a 10.0/20.0 grouping worth 180.0
total; the old code gives 300.0/110.0). Re-verified end to end against the real Olist data: the
same dashboard component now sums to exactly **729.39**. Ported identically into
`poc3_dashboard_generation`'s vendored copy of this file in the same change, so both POCs' engines
agree again.

## Finding 9 (fixed) — found one layer up again, in POC 3's full-scale run

Same relationship as Finding 8: `poc3_dashboard_generation` vendors this file's `SQLCompiler`/
`FieldResolver`/`registry.py` unmodified and ran them against the *full* ~99.4k-order real Olist
dataset through a real Groq-driven `DashboardPlanner` (its own `docs/real_world_integration_test.md`,
"Finding 5," has the full writeup). `repeat_purchase_rate` came back exactly **0.0** — not
implausibly low, structurally guaranteed to be zero, because `HAVING_RATIO`'s `group_by_field`
resolved in the same entity as its measures (`orders`), where real Olist rows carry only a
per-order surrogate `customer_id`, never the real per-person `customer_unique_id` (which exists
only on `customers`, one join away, and even there collides with the surrogate under the same
canonical name — see `FieldResolver`'s docstring). Grouping by a column unique to every order makes
"≥2 orders per group" structurally impossible: mechanically correct, semantically wrong,
validator-approved either way.

**Fixed** by adding two new optional `MetricDefinition` fields (`group_by_entity`,
`group_by_source_column`, both `None`/no-op by default) that let a `HAVING_RATIO` metric group by
a field in a DIFFERENT entity than its measures, reached via a single-hop join, and pick a specific
physical column directly rather than trusting `resolve_field`'s highest-confidence tie-break. The
join direction used here (the measures' entity holds the FK, joining out to the grouping entity) is
safe by construction — the opposite direction from Finding 4/8's fan-out risk, so no de-duplication
is needed. The ratio's denominator also had to change for this case: it's now `COUNT(*)` over the
grouping CTE itself (already grouped on the real key) rather than a separately-configured measure
query that would otherwise still count the surrogate — a no-op for the existing same-entity path.
`examples/real_olist_integration/run_real_test.py` and
`examples/real_olist_full_scale/run_full_scale_test.py` now set both new fields on
`repeat_purchase_rate`. Regression tests in `tests/test_having_ratio_cross_entity.py` (a synthetic
fixture reproducing the exact conflation shape) lock in the bug's symptom, the fix's correct
non-zero result, and that the existing same-entity path stays byte-identical. Ported identically
into `poc3_dashboard_generation`'s vendored copies in the same change.

**Confirmed against real data at full scale, not just the regression fixture**: a second full-scale
POC 3 run after the fix landed came back with `repeat_purchase_kpi` = 0.031187562437562436 (≈3.1%)
— a real, plausible number for this dataset (publicly known to have a very low per-person
repeat-purchase rate), replacing the previous structurally-guaranteed 0.0. `total_revenue_kpi` on
that same run still matched the reference number exactly, confirming the fix touched nothing else.

## What this test does and doesn't prove

Part 1 confirms the POC1 → POC2 handoff works end to end against real (if small) LLM-produced
output. Part 2 confirms it holds up at real Olist scale (~99.4k orders) and surfaced two more real
bugs that only existed because of *this specific dataset's* real data-quality characteristics (an
order missing its payment record; real timestamps landing on real range boundaries) — both fixed
and regression-tested. Findings 8 and 9 show this file's own testing wasn't the end of the story
either: both times, a bug in this engine surfaced one layer up, through POC 3's real-Groq-run
testing — once against a differently-shaped join (Finding 8), once against a metric kind
(`HAVING_RATIO`) this file's own tests never grouped cross-entity at all (Finding 9). Across all
four rounds: nine real gaps found, eight fixed, one (`revenue_growth`'s cross-entity join, finding
5) left as a documented, scoped-out backlog item rather than patched under time pressure,
consistent with POC 1's own "don't fix reactively mid-POC" practice. What's still not tested: a
semantic mapping with categories of imperfection this dataset didn't happen to contain (e.g. a
field POC 1 mapped to entirely the wrong canonical name, not just an ambiguous or missing one),
query latency under concurrent/repeated load rather than one query at a time, and a cross-entity
`GROUP BY` fan-out (Finding 4/8's risk direction) combined with a `RATIO`/`HAVING_RATIO` metric
kind (grouping a non-`BASE` metric by a dimension is already rejected outright for plain
`GROUP_BY`, and Finding 9's own cross-entity join is the opposite, safe direction, so this
combination remains currently unreachable, not separately verified).
