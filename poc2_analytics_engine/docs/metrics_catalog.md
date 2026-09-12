# E-Commerce Metrics Catalog — Hierarchical View

> Companion to `hld.md` and `supported_metrics.md` in this folder. Same research pass as the
> flat-table version kept in the Claude project (`metrics_catalog.md`), restructured as
> **Category → Bucket → Metric** so it doubles as a build-order map, not just a reference list.
> Near-duplicate definitions surfacing under different names across sources (e.g. "Warehouse
> Turnover Rate" vs. "Inventory Turnover Rate") are merged and noted as aliases rather than
> listed twice — so the true count below lands a little under the "~90" figure quoted earlier,
> once real duplicates are collapsed.
>
> Bucket legend: **B1** pure composition of existing primitives (registry entry only) · **B2**
> needs one new Tier 1 measure first · **B3** needs a new Tier 1 operator (window function,
> HAVING filter, percentile, ...) · **B4** a structurally different metric family
> (multi-step/model-based). Domain legend: **CORE** (Order/Customer/Product, POC 1's model) vs.
> **EXT-*** (a data domain not yet in the semantic model — mkt/web/inv/log/sup/loy/srch/survey/fin).

---

## A. Core Sales & Revenue

### B1 — pure composition / base measure
- **Revenue** — `SUM(order.revenue)` (CORE)
- **Orders** — `COUNT(DISTINCT order.order_id)` (CORE)
- **Active Customers** — `COUNT(DISTINCT order.customer_id)` (CORE)
- **Average Order Value (AOV)** — `Revenue / Orders` (CORE)
- **Purchase Frequency** — `Orders / Customers` (CORE)
- **Product Performance** — revenue grouped by product (CORE)
- **Product Category Performance** — revenue grouped by category (CORE)
- **Regional Performance** — revenue grouped by region (CORE)
- **Product Innovation Rate** — `COUNT(products WHERE launch_date IN period)` (CORE)
- **Return on Investment (ROI)** — `Net Profit / Cost of Investment` — B1 once Net Profit exists (CORE + EXT-fin)

### B2 — needs a new Tier 1 measure
- **Units Sold / Sales Volume** — `SUM(order_line.quantity)` (CORE)
- **Gross Margin** — `Revenue − COGS`, once `product.cost` is registered (CORE)
- **Net Profit / Operating Margin** — `Revenue − all expenses`, needs cost measures beyond COGS (CORE + EXT-fin)

### B3 — needs a new Tier 1 operator
- **Revenue Growth** — needs the growth-over-time operator (CORE)
- **Order Growth** — same operator (CORE)
- **Customer Growth** — same operator (CORE)

---

## B. Customer, Retention & Loyalty

### B1 — pure composition / base measure
- **Customer Churn Rate** — `100 − Retention Rate` (CORE)
- **Customer Loyalty Rate** — `Loyal customers / Total customers` (CORE/EXT-loy depending on definition used)
- **Historical CLV** — `SUM(revenue) GROUP BY customer`, observed to date (CORE)
- **Average Customer Value (ACV)** — total spend per customer over a period (CORE) — same shape as Historical CLV, scoped to a window
- **CLV : CAC Ratio** — `CLV / CAC` (CORE + EXT-mkt)
- **LTV : CAC Ratio** — same ratio, marketing-framed (CORE + EXT-mkt)
- **Net Promoter Score (NPS)** — `%promoters − %detractors` (EXT-survey)
- **Customer Satisfaction Score (CSAT)** — average survey score (EXT-survey)
- **Customer Service Satisfaction Score** — CSAT scoped to support interactions (EXT-sup)
- **Redemption Rate** — `Points redeemed / Points issued` (EXT-loy)
- **Tier Upgrade Rate** — `Members upgraded / Total members` (EXT-loy)
- **Participation Rate** — `Active loyalty members / Total customers` (EXT-loy)
- **Membership Activation Rate** — `Members who took ≥1 action / Members joined` (EXT-loy)
- **Attrition Rate (loyalty)** — `Members who stopped engaging / Total members` (EXT-loy)
- **Referral Conversion Rate** — `Referred customers who purchased / Referred customers` (EXT-loy)
- **Review Submission Rate** — `Customers who left a review / Customers who purchased` (EXT-review)
- **Tier Engagement Rate** — `Members engaging with tier perks / Members in tier` (EXT-loy)
- **Social Engagement (loyalty)** — interactions by loyalty members on social platforms (EXT-social)
- **Loyalty Program ROI** — `(Revenue from program − Program cost) / Program cost` (EXT-loy)
- **Points Liability** — `SUM(unredeemed points × point value)` (EXT-loy)

### B2 — needs a new Tier 1 measure
- **Incremental Revenue from Loyalty Members** — `Revenue from members − Revenue from non-members` (EXT-loy)
- **Cost to Serve** — retention/operational cost per customer (EXT-fin)

### B3 — needs a new Tier 1 operator
- **Repeat Purchase Rate** — needs the HAVING/threshold operator: `customers WHERE order_count ≥ 2` (CORE)
- **New vs. Returning Customer Revenue Split** — needs a "first order per customer" window function (CORE)
- **Customer Retention Rate** — needs a cross-period set-intersection operator (CORE)

### B4 — new metric family
- **Predictive CLV** — probabilistic model (e.g. BG/NBD) projecting future value (CORE data, B4 compute)
- **RFM Segmentation** — Recency, Frequency, and Monetary each scored 1–5, combined into a segment label (CORE)
- **Cohort Retention Curve** — retention % by "months since first purchase," per acquisition cohort (CORE)
- **Cohort Payback Period** — months for a cohort's cumulative margin to equal its acquisition cost (CORE + EXT-mkt)
- **Switch Ratio** — share of customer spend vs. competitors — likely infeasible, data isn't observable from our own side

---

## C. Marketing & Acquisition

### B1 — pure composition / base measure
- **Traffic Volume** — total sessions/visits (EXT-web)
- **Unique Visitors** — distinct visitors (EXT-web)
- **Bounce Rate** — `Single-page sessions / Total sessions` (EXT-web)
- **Average Time on Site** — mean session duration (EXT-web)
- **Page Views per Visit** — `Total page views / Total visits` (EXT-web)
- **New vs. Returning Visitors** — visitor split by first vs. repeat visit (EXT-web)
- **Mobile vs. Desktop Traffic** — visitor split by device (EXT-web)
- **Traffic Sources** — sessions grouped by acquisition channel (EXT-web)
- **Social Media Engagement (marketing)** — likes/comments/shares (EXT-social)
- **Email Open Rate** — `Opens / Emails sent` (EXT-web/email)
- **Email Click-Through Rate** — `Clicks / Emails sent` (EXT-web/email)
- **Conversion Rate** — `Orders / Sessions` (CORE + EXT-web)
- **Click-Through Rate (Ads)** — `Ad clicks / Ad impressions` (EXT-mkt)
- **Customer Acquisition Cost (CAC)** — `Marketing spend / New customers` (EXT-mkt)
- **Blended CAC** — all-in spend across channels / new customers (EXT-mkt)
- **Cost per Acquisition (CPA)** — `Spend / Leads or conversions` (EXT-mkt)
- **Return on Ad Spend (ROAS)** — `Ad revenue / Ad spend` (EXT-mkt)
- **Media Efficiency Ratio (MER)** — `Total revenue / Total ad spend` (CORE + EXT-mkt)

### B2 — needs a new Tier 1 measure
- **LTV-based ROAS** — combines LTV with ad spend rather than immediate order revenue (CORE + EXT-mkt)

### B4 — new metric family
- **CAC Payback Period** — time for cumulative margin from a customer to equal CAC (CORE + EXT-mkt)
- **Multi-touch Attribution** — revenue credit split across a customer's touchpoint sequence (EXT-mkt + EXT-web)
- **Ad Quality Score** — platform-computed composite (relevance, landing page, expected CTR) — largely out of scope, computed by the ad platform itself

---

## D. Product & Merchandising

### B1 — pure composition / base measure
- **Product Reviews (count)** — `COUNT(reviews)` per product (EXT-review)
- **Product Ratings (average)** — `AVG(rating)` per product (EXT-review)

### B2 — needs a new Tier 1 measure
- **Return Rate** — `Returned units / Units sold`, needs a `return` entity/flag (CORE-adjacent)
- **Upsell Rate** — `Upsell line items / Total order lines`, needs an upsell flag (CORE-adjacent)
- **Cross-sell Rate** — same shape, cross-sell flag (CORE-adjacent)

---

## E. Inventory & Supply Chain

### B1 — pure composition / base measure
- **Sell-through Rate** — `Units sold / Units received` (EXT-inv)
- **Stock-to-Sales Ratio** — `Ending inventory value / Total sales for period` (EXT-inv)
- **Backorder Rate** — `Delayed orders due to backorder / Total orders` (EXT-inv)
- **Inventory Turnover Rate** — `COGS / Average inventory` — alias: "Warehouse Turnover Rate" (EXT-inv)
- **Weeks on Hand** — `Current inventory / Average weekly sales` (EXT-inv)
- **Days Sales in Inventory (DSI)** — `(Average inventory / COGS) × 365` (EXT-inv)
- **Average Inventory** — `(Beginning + Ending inventory) / 2` (EXT-inv)
- **Fill Rate** — `(Total items − Unshipped items) / Total items` (EXT-inv)
- **Inventory Shrinkage** — `(Recorded − Actual) / Recorded` (EXT-inv)
- **Inventory Accuracy** — `Items matching records / Total items counted` (EXT-inv)
- **Stockout Rate** — `Out-of-stock items / Total inventory items` (EXT-inv)
- **Service Level** — `Orders delivered / Orders received` (EXT-inv)
- **Dead Stock Ratio** — `Unsellable inventory / Total inventory` (EXT-inv)
- **GMROI** — `Gross margin / Average inventory cost` (EXT-inv + CORE)
- **Lead Time** — `Order processing time + Supplier processing time + Delivery time` (EXT-inv)
- **On-time Orders** — `Orders shipped on time / Total orders` (EXT-log)
- **Warehouse Utilization Rate** — `Volume stored / Total capacity` (EXT-inv)

### B2 — needs a new Tier 1 measure
- **Inventory Carrying Cost** — `Storage + Insurance + Obsolescence + Opportunity cost`, needs each cost component as a measure (EXT-inv + EXT-fin)

### B3 — needs a new Tier 1 operator
- **Perfect Order Rate** — composite boolean measure: `AVG(CASE WHEN on_time AND complete AND undamaged AND accurate THEN 1 ELSE 0 END)` (EXT-inv + EXT-log)

### B4 — new metric family
- **Demand Forecast Accuracy** — `1 − |Actual − Forecast| / Actual`, needs a forecasting model to compare against (EXT-inv)

---

## F. Operations & Fulfillment

### B1 — pure composition / base measure
- **Shipping Cost** — `SUM(shipping cost)` per order (EXT-log)
- **Shipping Time** — `AVG(delivered_at − shipped_at)` (EXT-log)
- **Shipping Accuracy** — `Accurately shipped orders / Total orders` (EXT-log)
- **Shipping Damage Rate** — `Damaged orders / Total orders` (EXT-log)
- **Return Shipping Cost** — `SUM(return shipping cost)` (EXT-log)
- **Return Processing Time** — `AVG(return resolved_at − return initiated_at)` (EXT-log/EXT-sup)
- **Cart Abandonment Rate** — `1 − (Completed carts / Initiated carts)` (EXT-web)
- **Checkout Abandonment Rate** — `1 − (Completed checkouts / Initiated checkouts)` (EXT-web)
- **Order Abandonment Rate** — `Abandoned orders / Total orders` (EXT-web)
- **Payment Acceptance Rate** — `Orders with successful payment / Total orders` (CORE-adjacent — needs a payment-status field)

### B2 — needs a new Tier 1 measure
- **Order Processing Time** — `AVG(ship_ready_at − ordered_at)`, needs both timestamps registered (CORE-adjacent/EXT-log)
- **Order Fulfillment Time** — `AVG(delivered_or_picked_up_at − ordered_at)` (CORE-adjacent/EXT-log)
- **Order Delivery Time** — `AVG(received_at − ordered_at)` (CORE-adjacent/EXT-log)
- **Order Completion Rate** — needs a granular order-status field, not just "completed" orders (CORE-adjacent)
- **Order Cancellation Rate** — same status-field dependency (CORE-adjacent)

---

## G. Customer Service

### B1 — pure composition / base measure
- **Complaint Rate** — `Complaints / Customers` (EXT-sup)
- **Complaint Resolution Time** — `AVG(resolved_at − opened_at)` (EXT-sup)
- **Customer Service Response Time** — `AVG(first_response_at − opened_at)` (EXT-sup)
- **Customer Service Resolution Rate** — `Resolved tickets / Total tickets` (EXT-sup)
- **Customer Service Cost** — `SUM(support wages + tools + overhead)` (EXT-fin)

---

## H. Financial / Profitability

### B1 — pure composition / base measure
- **Warehouse Cost per Unit** — `Total warehouse expenses / Units stored` (EXT-fin)
- **Warehouse Labor Cost** — `SUM(wages + benefits)` for warehouse staff (EXT-fin)
- **Supplier Pricing** — cost of goods/services from a supplier (EXT-fin)
- **Supplier Diversity** — `% of spend with diverse-owned suppliers` (EXT-fin)

### B2 — needs a new Tier 1 measure
- **Contribution Margin (per channel / SKU)** — `Revenue − all variable costs`, each cost component registered first (CORE + EXT-fin + EXT-mkt)
- **Effective Discount Rate** — `Total discount value / Total gross revenue`, needs discount amount as a measure (CORE-adjacent)

### B4 — new metric family
- **Supplier Performance** — composite of delivery time, quality, and pricing — qualitative/composite, not a single aggregation (EXT-fin)
- **Warehouse Efficiency** — composite of speed, accuracy, space utilization, resource use (EXT-inv + EXT-fin)

---

## I. Site Search & Engagement

### B1 — pure composition / base measure
- **"No Results" / Zero-Results Rate** — `Searches with no matches / Total searches` (EXT-srch)
- **Search Exit Rate** — `Exits after search / Total search pageviews` (EXT-srch)
- **Search Conversion Rate** — `Orders following a search / Total searches` (EXT-srch)
- **Search Click-Through Rate** — `Clicks on search results / Search impressions` (EXT-srch)
- **Mobile Conversion Rate** — `Mobile conversions / Total mobile visitors` (EXT-web)

---

## J. Advanced / Statistical (cross-cutting — applies to any measure, not a fixed list)

### B1 — already free
- **Rank / Top-N** — already expressible via the AST's existing `sort` + `limit` fields, no new operator needed

### B3 — needs a new Tier 1 operator (cheaper than it sounds — mostly native SQL aggregates)
- **Share of Total** — `value / SUM(value) OVER ()` — window function
- **Moving Average / Rolling Window** — SQL window function, native in DuckDB/Postgres
- **Percentile / Median** — `PERCENTILE_CONT`, native in both engines
- **Standard Deviation / Variance** — `STDDEV`/`VARIANCE`, native aggregates
- **Correlation** — `CORR()`, native aggregate

### B4 — new metric family
- **Anomaly Detection** — baseline + deviation logic, a detector rather than a query
- **Statistical Significance / A/B Lift** — hypothesis testing, not an aggregation

---

## Roll-up: metric count by bucket

| Bucket | Count (approx.) | What it means for effort |
|---|---|---|
| B1 | ~60 | Registry entries only, once the underlying field/domain exists |
| B2 | ~15 | One new measure registration each, then B1 |
| B3 | ~12 | A handful of reusable operators (growth, HAVING, window functions) cover all of these |
| B4 | ~12 | Each is its own workstream — not registry rows |

The B1 count looks large, but most of it is gated on `EXT-*` domains that don't exist in the
semantic model yet — cheap in compiler terms, blocked on schema-discovery work in practice. The
`CORE`-domain B1/B2/B3 items (Category A, plus a handful in B/D/F) are what's actually reachable
without extending POC 1's scope, and match `supported_metrics.md`'s POC 2 selection closely.
