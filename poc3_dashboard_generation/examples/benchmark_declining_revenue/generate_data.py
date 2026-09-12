"""Generates the "declining revenue" benchmark dataset for POC 3's evaluation step
(docs/hld.md: "A declining-revenue case and a retention-problem case need to be constructed or
sourced before the evaluation step ... can run" -- an open question until now).

This is a SYNTHETIC dataset, not real data (unlike examples/real_olist_integration/) -- the point
here isn't testing against real-world messiness, it's giving `DashboardPlanner` a business
scenario with a known, deliberately-engineered shape (a clear revenue decline) so its output can
be judged against a rubric: does it notice the decline, pick components that actually show it, and
avoid emphasizing something less important instead? Same entity/field shape as this POC's own
`data/` sample dataset (orders/customers/products, `bootstrap_registry()` reused unchanged) so no
new registry code is needed -- only the data differs.

**The shape, deliberately:** 240 days of orders. Daily order volume declines steadily (~9/day ->
~2/day) with Gaussian noise, and Electronics' share of that (already-shrinking) volume shrinks
fastest (45% -> 10%) while the rest of the catalog holds up relatively better -- a common real
pattern: one product line going stale, not a uniform slowdown. `revenue_growth`, `order_growth`,
AND `customer_growth` (the last 90 days vs. the preceding 90, `SignalGatherer`'s own window) all
come out clearly and consistently negative, so this case tests whether the planner correctly
identifies decline as the dashboard's leading narrative -- not whether it can spot a single
ambiguous metric. `repeat_purchase_rate` is deliberately kept in a plausible, unremarkable range
(a ~12%-weighted "loyal" customer cohort keeps it moderate, not the focus of this case) so it
doesn't compete with the intended revenue-decline story -- see
examples/benchmark_retention_problem/ for the inverted scenario, where growth looks healthy but
retention is the real problem.

Every number reported by the real engine against this data can be checked against
`data/expected_values.json`, computed independently here in plain Python (no engine, no SQL, no
DuckDB) at generation time -- same "independently verify, don't trust the thing under test to
grade itself" discipline as every other dataset in this project. Re-running this script is
deterministic (fixed seed) and reproduces byte-identical CSVs and expected values.
"""
import csv
import json
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data"
RAW = OUT / "raw"

SEED = 20260912
NUM_DAYS = 240
START = date(2025, 1, 1)
REGIONS = ["North", "South", "East", "West"]
NUM_CUSTOMERS = 700
LOYAL_CUSTOMER_FRACTION = 0.12
LOYAL_CUSTOMER_WEIGHT = 6.0
CATEGORIES = {
    "Electronics": ["Widget", "Gizmo", "Sensor"],
    "Home": ["Lamp", "Rug", "Mug"],
    "Apparel": ["Jacket", "Shirt", "Cap"],
    "Grocery": ["Coffee", "Tea", "Snack Box"],
}


def generate():
    random.seed(SEED)
    RAW.mkdir(parents=True, exist_ok=True)

    days = [START + timedelta(days=i) for i in range(NUM_DAYS)]
    reference_date = days[-1]

    customers = [f"C{i+1}" for i in range(NUM_CUSTOMERS)]
    customer_region = {c: random.choice(REGIONS) for c in customers}
    customer_weight = {c: (LOYAL_CUSTOMER_WEIGHT if random.random() < LOYAL_CUSTOMER_FRACTION else 1.0) for c in customers}

    products, product_category, product_price = [], {}, {}
    pid = 1
    for cat, names in CATEGORIES.items():
        for _name in names:
            p = f"P{pid}"
            products.append(p)
            product_category[p] = cat
            product_price[p] = round(random.uniform(15, 120), 2)
            pid += 1

    orders = []
    order_id = 1
    for day_idx, day in enumerate(days):
        progress = day_idx / (NUM_DAYS - 1)  # 0 at start, 1 at end
        base_rate = 9.0 - 7.0 * progress  # ~9/day -> ~2/day
        n_orders_today = max(0, round(random.gauss(base_rate, 1.2)))

        electronics_share = 0.45 - 0.35 * progress  # 45% -> 10%
        other_share = (1 - electronics_share) / 3
        cat_weights = {"Electronics": electronics_share, "Home": other_share, "Apparel": other_share, "Grocery": other_share}

        for _ in range(n_orders_today):
            cat = random.choices(list(cat_weights.keys()), weights=list(cat_weights.values()))[0]
            prod = random.choice([p for p in products if product_category[p] == cat])
            cust = random.choices(customers, weights=[customer_weight[c] for c in customers])[0]
            revenue = round(product_price[prod] * random.uniform(0.9, 1.15), 2)
            orders.append({"order_id": f"O{order_id}", "customer_id": cust, "product_id": prod, "revenue": revenue, "order_date": day.isoformat()})
            order_id += 1

    with open(RAW / "orders.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["order_id", "customer_id", "product_id", "revenue", "order_date"])
        w.writeheader()
        w.writerows(orders)
    with open(RAW / "customers.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["customer_id", "name", "region"])
        w.writeheader()
        w.writerows({"customer_id": c, "name": f"Customer {c}", "region": customer_region[c]} for c in customers)
    with open(RAW / "products.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["product_id", "name", "category"])
        w.writeheader()
        w.writerows({"product_id": p, "name": p, "category": product_category[p]} for p in products)

    _write_expected_values(orders, product_category, reference_date)
    return orders


def _write_expected_values(orders, product_category, reference_date):
    """Independent (no engine) computation of every number `tests/test_benchmark_datasets.py`
    checks the real pipeline against -- mirrors SignalGatherer's own 90-day-vs-90-day growth
    window exactly (see src/insightflow/dashboard/signal.py's `_growth_windows`), since that's the
    window the real dashboard run will actually use; everything else (totals, repeat rate,
    category revenue) is a plain aggregate with no engine-specific behavior to mirror."""

    def in_window(order_date_str, start, end):
        return start <= date.fromisoformat(order_date_str) <= end

    current_end = reference_date
    current_start = current_end - timedelta(days=89)
    comparison_end = current_start - timedelta(days=1)
    comparison_start = comparison_end - timedelta(days=89)

    def window_stats(start, end):
        rows = [o for o in orders if in_window(o["order_date"], start, end)]
        return (
            sum(o["revenue"] for o in rows),
            len({o["order_id"] for o in rows}),
            len({o["customer_id"] for o in rows}),
        )

    cur_rev, cur_orders, cur_cust = window_stats(current_start, current_end)
    cmp_rev, cmp_orders, cmp_cust = window_stats(comparison_start, comparison_end)

    total_revenue = round(sum(o["revenue"] for o in orders), 2)
    total_orders = len(orders)
    total_customers = len({o["customer_id"] for o in orders})

    orders_per_customer = defaultdict(int)
    for o in orders:
        orders_per_customer[o["customer_id"]] += 1
    repeat_customers = sum(1 for _c, n in orders_per_customer.items() if n >= 2)

    cat_revenue = defaultdict(float)
    for o in orders:
        cat_revenue[product_category[o["product_id"]]] += o["revenue"]

    expected = {
        "reference_date": reference_date.isoformat(),
        "total_revenue": total_revenue,
        "total_orders": total_orders,
        "total_customers": total_customers,
        "revenue_growth": (cur_rev - cmp_rev) / cmp_rev if cmp_rev else None,
        "order_growth": (cur_orders - cmp_orders) / cmp_orders if cmp_orders else None,
        "customer_growth": (cur_cust - cmp_cust) / cmp_cust if cmp_cust else None,
        "repeat_purchase_rate": round(repeat_customers / total_customers, 4),
        "category_revenue": {k: round(v, 2) for k, v in cat_revenue.items()},
    }
    (OUT / "expected_values.json").write_text(json.dumps(expected, indent=2) + "\n")
    return expected


if __name__ == "__main__":
    orders = generate()
    expected = json.loads((OUT / "expected_values.json").read_text())
    print(f"generated {len(orders)} orders -> {RAW}")
    print(json.dumps(expected, indent=2))
