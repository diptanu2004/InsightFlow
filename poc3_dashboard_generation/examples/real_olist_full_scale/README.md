# Real-world SCALE integration test: the full ~99.4k-order Olist dataset

The last item before POC 3 can be considered closed (see top-level `README.md`'s "POC 3 status").
`examples/real_olist_integration/` already proved the real-Groq-call path end to end against an
8-order referential subset, including the join-fan-out fix
(`docs/real_world_integration_test.md`'s "Finding 4"). This example asks the same question POC 2's
own `examples/real_olist_full_scale/` asked one layer down: does everything still hold up at real
row counts (~99.4k orders across 7 CSVs), not just a handful?

## Deliberate exception to this project's "self-contained POC" rule

Every other POC 3 example copies its data byte-for-byte into its own directory. This one doesn't:
the full dataset is ~50MB, and the device bridge used to move files into/out of this project's
cloud sandbox has already failed repeatedly on anything over ~2MB
(`poc2_analytics_engine/docs/real_world_integration_test.md`'s "Part 2"). Copying it a second time
would hit the same wall for no benefit, since this script -- like every real-Groq-call script in
this project -- can only actually be *run* on your own machine anyway (no `GROQ_API_KEY` in the
cloud sandbox). `run_full_scale_test.py`'s `DATA_DIR` points at
`poc2_analytics_engine/examples/real_olist_full_scale/data/` directly, a sibling POC folder on
disk (not a package import -- still no cross-venv dependency), by explicit decision. Pass
`--data-dir` if that sibling-folder assumption ever stops holding.

## Run it

```
export GROQ_API_KEY=...     # or rely on the .env file already in poc3_dashboard_generation/
uv run python examples/real_olist_full_scale/run_full_scale_test.py
```

Registry is copied unchanged from `examples/real_olist_integration/run_real_test.py` -- same
judgment calls apply (see that file's inline comments). Unlike the small-scale example, this
script does NOT hardcode a `reference_date`: it queries `MAX(orders.transaction_date)` directly off
the CSV at startup (see `_discover_reference_date`'s own docstring for why -- the exact max date of
whatever full-scale download you have isn't something this sandbox can verify, so it's computed at
runtime instead of assumed).

This script's wiring (engine construction, reference-date discovery, `SignalGatherer`) was smoke-
tested in the cloud sandbox against the small 8-order subset before being handed off -- everything
up to the real Groq call is verified working; the actual run at full scale still needs to happen on
your machine, since that's the one thing this sandbox can't do.

## What to check in the output

- **No crash, no rejection** -- the vendored engine (join-fan-out fix included) and
  `DashboardValidator` holding up at ~99.4k orders is the main thing this test is for.
- **Numbers are plausible at this scale** -- cross-reference against
  `poc2_analytics_engine/docs/real_world_integration_test.md`'s "Part 2" table (revenue ≈
  16,008,872.12, orders = 99,441, etc.) for the point metrics; any KPI wildly off that isn't
  explained by a difference in registry/query shape is worth investigating before calling this
  closed.
- **Timing** -- the printed `pipeline build` / `dashboard generation` timings are the first real
  signal of whether `DashboardGenerationPipeline` (five real DuckDB queries via `SignalGatherer`,
  one real Groq call, then N more real DuckDB queries via `DashboardDataResolver`) is fast enough
  to matter for a future FastAPI route, not just correct.

## Status

**Run twice.** First run: `revenue`/`orders` matched `poc2_analytics_engine`'s own full-scale
reference numbers exactly (16,008,872.12 / 99,441), the vendored engine and `DashboardValidator`
held up cleanly at ~99.4k rows (no crash, no rejection, 5 validator-approved components), and
pipeline build (468ms) + full dashboard generation (5.7s, including the real Groq call) both look
fast enough for a future FastAPI route. It also found one real correctness bug: `repeat_purchase_kpi`
computed exactly 0.0 because `HAVING_RATIO`'s default grouping resolved to the per-order surrogate
`customer_id` rather than the real `customer_unique_id` — see `docs/real_world_integration_test.md`'s
"Finding 5" for the full write-up and fix.

**Second run, after the fix**: `repeat_purchase_kpi` now comes back **0.031187562437562436** (≈3.1%)
— a real, plausible number for this dataset (Olist's real per-person repeat-purchase rate is
publicly known to be very low), not the previous structurally-guaranteed 0.0.
`total_revenue_kpi` still matched the reference number exactly, confirming the fix didn't disturb
anything else. The dashboard's `narrative` this run also explicitly connected the (negative)
order/customer growth signals to the low repeat rate into a stated "sustainability risk" verdict,
rather than listing them independently — early real-world evidence that the planner-prompt
reconciliation fix (added after the `benchmark_retention_problem` case) works, even though this
isn't the exact case that found that gap. Minor, non-blocking note: 3 of 5 components this run
grouped by the same single dimension (`price`) with different metrics/chart types — not wrong
(still the only unambiguous real dimension available), just less varied than the first run's 2-of-5.
