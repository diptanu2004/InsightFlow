"""Generates the "retention problem" benchmark dataset for POC 3's evaluation step (the other
half of docs/hld.md's open "benchmark semantic models" item -- see
examples/benchmark_declining_revenue/generate_data.py's module docstring for the shared rationale
and the "independently verify" discipline this follows).

**The shape, deliberately -- a "leaky bucket," the inverse of the declining-revenue case.**
New-customer arrivals ramp UP over 240 days (~3/day -> ~9/day), so `revenue_growth`,
`order_growth`, and `customer_growth` (the last 90 days vs. the preceding 90) all come out clearly
POSITIVE -- the business looks healthy, even growing, on every top-line signal. But only 10% of
customers are ever capable of a second order (a fixed "loyal" cohort assigned at signup, each
scheduled for one repeat purchase 20-150 days later if that still falls inside the 240-day
window); the other 90% never return. `repeat_purchase_rate` comes out far below the ~0.5 "healthy"
baseline used elsewhere in this project -- almost the entire order/revenue/customer growth is new
acquisition covering for churn, not real business health. This tests something the
declining-revenue case can't: whether the planner surfaces `repeat_purchase_rate` as the real
story even when every growth number it's tempted to lead with looks good.

Same entity/field shape as this POC's own `data/` sample dataset (orders/customers/products,
`bootstrap_registry()` reused unchanged); every number the real engine reports against this data
can be checked against `data/expected_values.json`, computed independently in plain Python here.
Deterministic (fixed seed) -- re-running reproduces byte-identical output.
"""
import csv
import json
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data"
RAW = OUT / "raw"

SEED = 20260913
NUM_DAYS = 240
START = date(2025, 1, 1)
REGIONS = ["North", "South", "East", "West"]
LOYAL_CUSTOMER_FRACTION = 0.10
REPEAT_GAP_DAYS = (20, 150)  # a loyal customer's second order lands this many days after the first
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

    products, product_category, product_price = [], {}, {}
    pid = 1
    for cat, names in CATEGORIES.items():
        for _name in names:
            p = f"P{pid}"
            products.append(p)
            product_category[p] = cat
            product_price[p] = round(random.uniform(15, 120), 2)
            pid += 1

    customers, customer_region = [], {}
    orders = []
    order_id_counter = [1]

    def make_order(day, cust, prod):
        revenue = round(product_price[prod] * random.uniform(0.9, 1.15), 2)
        orders.append({"order_id": f"O{order_id_counter[0]}", "customer_id": cust, "product_id": prod, "revenue": revenue, "order_date": day.isoformat()})
        order_id_counter[0] += 1

    pending_repeat_orders = []  # (day_index_to_place, customer_id)
    cust_counter = 1
    for day_idx, day in enumerate(days):
        progress = day_idx / (NUM_DAYS - 1)  # 0 -> 1
        new_today = max(0, round(random.gauss(3.0 + 6.0 * progress, 1.0)))  # acquisition ramps up

        for _ in range(new_today):
            cust = f"C{cust_counter}"
            cust_counter += 1
            customers.append(cust)
            customer_region[cust] = random.choice(REGIONS)

            prod = random.choice(products)
            make_order(day, cust, prod)

            if random.random() < LOYAL_CUSTOMER_FRACTION:
                gap = random.randint(*REPEAT_GAP_DAYS)
                repeat_day = day_idx + gap
                if repeat_day < NUM_DAYS:
                    pending_repeat_orders.append((repeat_day, cust))

        for cust in [c for (d, c) in pending_repeat_orders if d == day_idx]:
            make_order(day, cust, random.choice(products))

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
    """Mirrors SignalGatherer's own 90-day-vs-90-day growth window exactly -- see the
    declining-revenue generator's twin docstring for why."""

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
