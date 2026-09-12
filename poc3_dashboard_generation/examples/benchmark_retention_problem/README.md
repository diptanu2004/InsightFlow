# Benchmark case: retention problem

The other benchmark semantic model `docs/hld.md` flagged as an open question -- see
`examples/benchmark_declining_revenue/README.md` for the shared rationale (synthetic, not real
data, deliberately-engineered shape, judged against a rubric rather than "does it look
plausible").

## The shape -- a "leaky bucket," the inverse of the declining-revenue case

`generate_data.py` builds 240 days of orders where new-customer acquisition ramps UP steadily
(~3/day -> ~9/day, see its own module docstring for full detail), so every growth signal
`SignalGatherer` computes (the last 90 days vs. the preceding 90) looks clearly HEALTHY:

```
revenue_growth   ≈ +0.40
order_growth     ≈ +0.40
customer_growth  ≈ +0.39
repeat_purchase_rate ≈ 0.05   <- the actual problem, invisible in any growth number above
```

Only 10% of customers are ever capable of a second order; the other 90% never return. Almost all
of the positive growth above is new acquisition covering for churn, not real business health.
(Exact numbers, plus per-category revenue, are in `data/expected_values.json`, computed
independently in plain Python at generation time.)

This case tests something the declining-revenue case can't: whether the planner surfaces
`repeat_purchase_rate` as the real story even when every growth number it's tempted to lead with
looks good -- `repeat_purchase_rate` isn't one of `SignalGatherer`'s pre-fetched fixed signals (it
takes no time filter, so it isn't a "growth" signal the way revenue/orders/customers are), but it
IS in `PlannerContext.resolvable_metrics`, so the planner has to actively choose to query it via a
KPI component rather than just repeating the pre-fetched growth signals it's handed.

## Regenerate the data

```
python3 generate_data.py
```

Deterministic (fixed seed). `tests/test_benchmark_datasets.py` (run from the POC 3 root) verifies
the real engine's numbers against `expected_values.json` exactly, and that `SignalGatherer`'s own
growth signals really do look healthy here while `repeat_purchase_rate`, queried directly,
confirms the underlying problem.

## Run the real benchmark (real Groq call, your machine only)

```
export GROQ_API_KEY=...     # or rely on the .env file already in poc3_dashboard_generation/
uv run python examples/benchmark_retention_problem/run_benchmark_test.py
```

No `GROQ_API_KEY` exists in the cloud sandbox this POC was built in -- run on your own machine and
paste the output back for evaluation.

## Evaluation rubric

Same three checks as `examples/benchmark_declining_revenue/README.md`, with the interesting
question being usefulness: does the dashboard include a `repeat_purchase_rate` KPI at all, and
does its narrative/rationale call out that top-line growth is masking a retention problem? A
dashboard that only reports the three positive growth signals and calls the business "healthy" has
missed the entire point of this benchmark case -- that's the failure mode this dataset exists to
catch, and it would slip past `DashboardValidator` cleanly (nothing about it is an unsupported
metric or a rejected spec), which is exactly why a human/rubric evaluation step is needed on top
of the mechanical one.

## Results

**Run twice, on the user's machine, both with real Groq calls.**

*First run.* Numbers matched `expected_values.json` exactly across every component (revenue,
category breakdown, `repeat_purchase_rate` = 0.0521). Absence-of-unsupported-metrics and
chart-selection both passed cleanly. **Usefulness failed**, exactly the way this case is designed
to catch it: the planner did query `repeat_purchase_rate` as a KPI (the mechanical part of the
test), but its rationale called it a neutral "loyalty" signal and the dashboard's `narrative` just
said it "highlights overall growth ... to guide strategic decisions" -- never naming that a 5%
repeat rate underneath 40% growth means the growth is almost entirely new-customer acquisition,
not retention. Led to the `DashboardPlanner` prompt fix in `planner.py` (instructs the planner to
explicitly reconcile any tension between a metric it queries and the pre-computed growth signals,
rather than describing them independently).

*Second run, after the fix.* Numbers still match exactly (`total_revenue_kpi` = 91,959.97,
`repeat_purchase_kpi` = 0.052083333333333336, category revenue digit-for-digit against
`expected_values.json`). **Usefulness now passes**: the narrative reads *"Revenue, orders, and
customers are all up around 40%, but the low repeat purchase rate shows growth is driven by new
customers rather than repeat business. Category and regional breakdowns reveal concentration in
Home and North, indicating a need to broaden repeat sales and diversify performance."* — a direct
statement of the tension this benchmark exists to test, not a generic growth summary, and the
`repeat_purchase_kpi` component's own rationale ("Low repeat purchase rate indicates growth may
rely on new customers rather than returning buyers") reinforces the same point rather than
describing it as a neutral loyalty metric. Chart selection also improved: this run added a
`region`-grouped pie chart and a product-level customer table alongside the category breakdown,
where the first run had only the category chart plus a per-customer-order table.

All three rubric checks now pass on this case. This is the one benchmark case in
`docs/hld.md`'s evaluation section that has a confirmed, real-Groq-verified pass end to end,
including the fix for the failure mode it was specifically built to catch.
