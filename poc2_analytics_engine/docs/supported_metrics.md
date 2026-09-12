# POC 2 — Supported Metrics

> Scope: the metrics POC 2 actually implements, drawn from the architecture doc's §9.1 initial
> list. This is the small, concrete subset of the full research catalog
> (`metrics_catalog.md`, kept in the Claude project) that POC 2 is scoped to build — not a
> restatement of the catalog. See `hld.md` in this folder for how the two-tier Metric Registry
> these are defined against actually works.

## The metrics

| Metric | Formula | Bucket | Domain |
|---|---|---|---|
| Revenue | `SUM(order.revenue)` | B1 — Tier 1 measure | CORE |
| Orders | `COUNT(DISTINCT order.order_id)` | B1 — Tier 1 measure | CORE |
| Customers | `COUNT(DISTINCT order.customer_id)` | B1 — Tier 1 measure | CORE |
| Average Order Value (AOV) | `Revenue / Orders` | B1 — Tier 2, pure composition | CORE |
| Product / Category Performance | Revenue grouped by product or category dimension | B1 — dimension grouping (already expressible via the AST's `dimension` field) | CORE |
| Regional Performance | Revenue grouped by region dimension | B1 — same as above | CORE |
| Revenue Growth | `(Revenue_periodB − Revenue_periodA) / Revenue_periodA` | B3 — needs the **growth-over-time operator** | CORE |
| Order Growth | Growth operator applied to Orders | B3 — same operator as Revenue Growth | CORE |
| Customer Growth | Growth operator applied to Customers | B3 — same operator | CORE |
| Repeat Purchase Rate | `COUNT(customers WHERE order_count ≥ 2) / COUNT(customers)` | B3 — needs the **HAVING/threshold-filter operator** | CORE |

## What this means for build order

Everything in this list resolves against the Order/Customer/Product entities POC 1's example
semantic model already covers — nothing here is blocked on schema-discovery work or an
`EXT-*` data domain. That's by design: §9.1's initial metric set was chosen to be answerable from
data POC 1 already produces.

Six of the ten (Revenue, Orders, Customers, AOV, Product/Category Performance, Regional
Performance) are **B1** — a registered measure or a pure composition/grouping of one. These
should work as soon as the base measures are registered and the compiler can emit a basic
`SELECT ... GROUP BY` — no new primitives required.

The remaining four (Revenue Growth, Order Growth, Customer Growth, Repeat Purchase Rate) are
**B3** — but they only need **two** new Tier 1 operators between them, not four:

- **Growth-over-time**: evaluate a measure over two time windows and return the delta/percentage
  change. Unblocks Revenue Growth, Order Growth, and Customer Growth simultaneously once built
  — it's the same operator applied to three different measures.
- **HAVING/threshold filter**: group by customer, then filter groups by a count threshold
  (`order_count >= 2`). Unblocks Repeat Purchase Rate, and is the same primitive later metrics
  in the full catalog (e.g. any "customers who did X at least N times" metric) will reuse.

So the practical build order is: get the three B1 base measures (`revenue`, `order_id` count,
`customer_id` count) and dimension grouping working end-to-end first (covers 6/10 metrics and
exercises the full AST → Validator → Compiler → DuckDB → MetricResult pipeline), then add the
growth-over-time operator (covers 3 more), then the HAVING/threshold operator (covers the last
one). By the time all ten are supported, both operators needed for the *next* wave of metrics in
`metrics_catalog.md` (repeat-purchase-style and period-comparison-style metrics generally)
already exist.

## Evaluation

Per the architecture doc §26 (POC 2 row), each of these ten needs a fixture: a hand-written AST,
a small known dataset, and a hand-computed expected value — the same numerical-correctness
harness pattern as POC 1's ground-truth benchmark, just for arithmetic instead of schema mapping.
