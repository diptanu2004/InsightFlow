"""Independent ground truth: hand-written DuckDB over the raw CSVs. Never read back from InsightFlow."""
import duckdb, json, os

ROOT = r"C:\Users\dipta\Desktop\InsightFlow\test_datasets"
SCRATCH = r"C:\Users\dipta\AppData\Local\Temp\claude\c--Users-dipta-Desktop-InsightFlow\52fa0126-9434-46ef-a148-ac3e6c78ff16\scratchpad"
con = duckdb.connect()


def R(ds, name):
    return "read_csv_auto('" + os.path.join(ROOT, ds, name).replace("\\", "/") + "', header=true)"


def q(sql):
    try:
        return con.execute(sql).fetchall()
    except Exception as e:
        return "ERROR " + str(e)[:200]


def one(sql):
    r = q(sql)
    return r if isinstance(r, str) else (r[0][0] if r and r[0] else None)


out = {}

# ---- chinook: digital music store. Invoice.Total is the paid amount.
inv, lines, cust = R("chinook", "invoices.csv"), R("chinook", "invoice_lines.csv"), R("chinook", "customers.csv")
out["chinook"] = {
    "revenue_invoice_total": one("select round(sum(Total),2) from " + inv),
    "revenue_line_items": one("select round(sum(UnitPrice*Quantity),2) from " + lines),
    "orders": one("select count(distinct InvoiceId) from " + inv),
    "customers_on_invoices": one("select count(distinct CustomerId) from " + inv),
    "customers_on_file": one("select count(distinct CustomerId) from " + cust),
    "date_min_max": q("select min(InvoiceDate), max(InvoiceDate) from " + inv),
    "top_country": q("select BillingCountry, round(sum(Total),2) r from " + inv + " group by 1 order by r desc limit 3"),
}
out["chinook"]["aov"] = round(out["chinook"]["revenue_invoice_total"] / out["chinook"]["orders"], 4)

# ---- pizza_place_sales: revenue is quantity x price; no single revenue column exists.
pd_, pz, pt, po = (R("pizza_place_sales", f) for f in ["order_details.csv", "pizzas.csv", "pizza_types.csv", "orders.csv"])
out["pizza_place_sales"] = {
    "revenue_qty_x_price": one(f"select round(sum(d.quantity*p.price),2) from {pd_} d join {pz} p on d.pizza_id=p.pizza_id"),
    "sum_price_on_pizzas_only": one("select round(sum(price),2) from " + pz),
    "orders": one("select count(distinct order_id) from " + po),
    "order_lines": one("select count(*) from " + pd_),
    "customers": "none - dataset has no customer identifier at all",
    "date_min_max": q("select min(date), max(date) from " + po),
    "by_category": q(
        f"select t.category, round(sum(d.quantity*p.price),2) r from {pd_} d join {pz} p on d.pizza_id=p.pizza_id "
        f"join {pt} t on p.pizza_type_id=t.pizza_type_id group by 1 order by r desc"
    ),
}

# ---- classic_models: one denormalized file, US dates, SALES is the line total.
cm = R("classic_models", "sales_data_sample.csv")
out["classic_models"] = {
    "revenue_sales": one("select round(sum(SALES),2) from " + cm),
    "orders": one("select count(distinct ORDERNUMBER) from " + cm),
    "customers": one("select count(distinct CUSTOMERNAME) from " + cm),
    "date_min_max": q("select min(strptime(ORDERDATE,'%m/%d/%Y %H:%M')), max(strptime(ORDERDATE,'%m/%d/%Y %H:%M')) from " + cm),
    "by_productline": q("select PRODUCTLINE, round(sum(SALES),2) r from " + cm + " group by 1 order by r desc"),
    "top_country": q("select COUNTRY, round(sum(SALES),2) r from " + cm + " group by 1 order by r desc limit 3"),
}
out["classic_models"]["aov"] = round(out["classic_models"]["revenue_sales"] / out["classic_models"]["orders"], 4)

# ---- supermarket_sales: POS receipts, Total includes 5% tax.
ss = R("supermarket_sales", "supermarket_sales.csv")
out["supermarket_sales"] = {
    "revenue_total": one('select round(sum("Total"),2) from ' + ss),
    "revenue_cogs": one('select round(sum("cogs"),2) from ' + ss),
    "sum_unit_price": one('select round(sum("Unit price"),2) from ' + ss),
    "orders": one('select count(distinct "Invoice ID") from ' + ss),
    "customers": "none - only 'Customer type' (Member/Normal), no customer identifier",
    "date_min_max": q("select min(strptime(\"Date\",'%m/%d/%Y')), max(strptime(\"Date\",'%m/%d/%Y')) from " + ss),
    "by_product_line": q('select "Product line", round(sum("Total"),2) r from ' + ss + " group by 1 order by r desc"),
    "by_city": q('select "City", round(sum("Total"),2) r from ' + ss + " group by 1 order by r desc"),
}
out["supermarket_sales"]["aov"] = round(out["supermarket_sales"]["revenue_total"] / out["supermarket_sales"]["orders"], 4)

# ---- bikestores: revenue is list_price x quantity x (1-discount); no revenue column.
oi, od, cu, pr, ca = (R("bikestores", f) for f in ["order_items.csv", "orders.csv", "customers.csv", "products.csv", "categories.csv"])
out["bikestores"] = {
    "revenue_net": one("select round(sum(list_price*quantity*(1-discount)),2) from " + oi),
    "revenue_gross_no_discount": one("select round(sum(list_price*quantity),2) from " + oi),
    "sum_list_price_only": one("select round(sum(list_price),2) from " + oi),
    "orders": one("select count(distinct order_id) from " + od),
    "customers_with_orders": one("select count(distinct customer_id) from " + od),
    "customers_on_file": one("select count(distinct customer_id) from " + cu),
    "date_min_max": q("select min(order_date), max(order_date) from " + od),
    "by_state": q(
        f"select c.state, round(sum(i.list_price*i.quantity*(1-i.discount)),2) r from {oi} i "
        f"join {od} o on i.order_id=o.order_id join {cu} c on o.customer_id=c.customer_id group by 1 order by r desc"
    ),
    "by_category": q(
        f"select cat.category_name, round(sum(i.list_price*i.quantity*(1-i.discount)),2) r from {oi} i "
        f"join {pr} p on i.product_id=p.product_id join {ca} cat on p.category_id=cat.category_id group by 1 order by r desc"
    ),
}
out["bikestores"]["aov"] = round(out["bikestores"]["revenue_net"] / out["bikestores"]["orders"], 4)

# ---- jaffle_shop
jo, jp = R("jaffle_shop", "raw_orders.csv"), R("jaffle_shop", "raw_payments.csv")
out["jaffle_shop"] = {
    "revenue_amount_raw": one("select sum(amount) from " + jp),
    "orders": one("select count(distinct id) from " + jo),
    "customers_with_orders": one("select count(distinct user_id) from " + jo),
    "date_min_max": q("select min(order_date), max(order_date) from " + jo),
}

# ---- northwind
no_, nd = R("northwind", "orders.csv"), R("northwind", "order_details.csv")
out["northwind"] = {
    "revenue_net": one("select round(sum(unitPrice*quantity*(1-discount)),2) from " + nd),
    "revenue_gross": one("select round(sum(unitPrice*quantity),2) from " + nd),
    "sum_unit_price_only": one("select round(sum(unitPrice),2) from " + nd),
    "freight_total": one("select round(sum(freight),2) from " + no_),
    "orders": one("select count(distinct orderID) from " + no_),
    "customers_with_orders": one("select count(distinct customerID) from " + no_),
    "date_min_max": q("select min(orderDate), max(orderDate) from " + no_),
    "top_country": q("select shipCountry, count(*) n from " + no_ + " group by 1 order by n desc limit 3"),
}

# ---- online_retail_ii
orii = R("online_retail_ii", "online_retail_ii.csv")
out["online_retail_ii"] = {
    "revenue_qty_x_price": one("select round(sum(Quantity*Price),2) from " + orii),
    "sum_price_only": one("select round(sum(Price),2) from " + orii),
    "orders": one("select count(distinct Invoice) from " + orii),
    "customers": one('select count(distinct "Customer ID") from ' + orii),
    "rows": one("select count(*) from " + orii),
    "date_min_max": q("select min(InvoiceDate), max(InvoiceDate) from " + orii),
    "top_country": q("select Country, round(sum(Quantity*Price),2) r from " + orii + " group by 1 order by r desc limit 3"),
}

# ---- superstore
so = R("superstore", "superstore_orders.csv")
out["superstore"] = {
    "revenue_sales": one('select round(sum("Sales"),2) from ' + so),
    "orders": one('select count(distinct "Order ID") from ' + so),
    "customers": one('select count(distinct "Customer ID") from ' + so),
    "date_min_max": q("select min(strptime(\"Order Date\",'%m/%d/%Y')), max(strptime(\"Order Date\",'%m/%d/%Y')) from " + so),
    "by_category": q('select "Category", round(sum("Sales"),2) r from ' + so + " group by 1 order by r desc"),
    "by_region": q('select "Region", round(sum("Sales"),2) r from ' + so + " group by 1 order by r desc"),
}
out["superstore"]["aov"] = round(out["superstore"]["revenue_sales"] / out["superstore"]["orders"], 4)

print(json.dumps(out, indent=2, default=str))
with open(os.path.join(SCRATCH, "ground_truth.json"), "w", encoding="utf-8") as fh:
    fh.write(json.dumps(out, indent=2, default=str))
