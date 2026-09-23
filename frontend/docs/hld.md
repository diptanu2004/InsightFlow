# InsightFlow Frontend — High-Level Design (Phase 8)

The React app is the first real user of the backend built in Phases 5–7. It follows the same
rule as everything else in the repo: **the LLM understands, deterministic systems calculate.**
The browser adds one more rule on top: **the UI never computes, infers, or restates a number.**
It renders `MetricResult`s the backend already verified, and it shows how each one was computed.

Status: M0–M6 done. M6 was a full end-to-end run through the browser against real
Postgres/MinIO/Redis and real Groq, checked against ground truth (see §9).

---

## 1. Stack

| Concern | Choice | Why |
|---|---|---|
| Build/dev | Vite 8, React 19, TypeScript ~5.9 | TS is pinned to 5.9 because `openapi-typescript@7` has a TS 5 peer dependency |
| Styling | Tailwind 4 (`@tailwindcss/vite`) | No component library to fight |
| Server state | TanStack Query 5 | Cache keys and invalidation in one file (`src/api/queries.ts`) |
| Routing | React Router 7 | |
| Charts | Recharts 3 | Used only for bars and pies; KPIs and tables are plain HTML |
| Lint | oxlint | |
| E2E | Playwright (`@playwright/test`) | Runs against the real stack only (§9) |

The template's `erasableSyntaxOnly` is on, so there are no enums and no constructor parameter
properties. TS has to compile by type erasure alone.

## 2. Layout

```
frontend/
├── src/
│   ├── api/
│   │   ├── openapi.json     # generated: dumped from the FastAPI app
│   │   ├── schema.d.ts      # generated: openapi-typescript
│   │   ├── types.ts         # short aliases over schema.d.ts (no hand-written shapes)
│   │   ├── client.ts        # fetch wrapper: token injection, refresh-on-401, ApiError
│   │   └── queries.ts       # every TanStack Query hook, one per route
│   ├── auth/                # tokens (localStorage), AuthProvider, useAuth, role ranking
│   ├── components/
│   │   ├── AppShell.tsx
│   │   ├── DatasetUpload.tsx      # upload + discovery job polling
│   │   ├── SemanticModelView.tsx  # mapping table + confirm/reject review
│   │   ├── dashboard/             # DashboardSection, DashboardView (renderer map), format.ts
│   │   └── chat/ChatPanel.tsx
│   ├── pages/               # Login, Organizations, Projects, Project
│   └── App.tsx              # routes + RequireAuth
├── e2e/olist-journey.spec.ts
├── playwright.config.ts
└── vite.config.ts           # /api proxy
```

Routes: `/login`, `/` (organizations), `/orgs/:orgId` (projects), `/projects/:projectId`
(data, dashboard, chat). All except `/login` sit behind `RequireAuth`.

## 3. API contract: generated, never hand-written

```
backend FastAPI app ──scripts/dump_openapi.py──▶ src/api/openapi.json ──openapi-typescript──▶ src/api/schema.d.ts
```

`npm run gen:api` runs both steps. The dump imports the app without any infrastructure; it sets a
placeholder `JWT_SECRET` only so `create_app()` will start. Both generated files are checked in,
so a backend change that alters a response shape shows up as a diff and as a TypeScript error.

`types.ts` only aliases generated types (`MetricResult`, `HydratedDashboard`, `Answer`,
`SemanticModel`, `FieldStatus`, `Role`, …). If a type is missing, fix the backend model and
regenerate. Don't add a local interface.

Records keyed by generated unions are exhaustive by construction. For example,
`RENDERERS: Record<ComponentType, …>` and `RANK: Record<Role, number>` stop compiling when the
backend adds a component type or a role.

## 4. Talking to the backend

**Proxy.** Backend paths such as `/projects/{id}` collide with UI routes, so the app calls
`/api/*`. In dev, Vite proxies that to `:8000` and strips the prefix. Requests are same-origin, so
the backend's `CORS_ALLOWED_ORIGINS` stays empty (it fails closed). To use a remote backend, set
`VITE_API_BASE_URL` and add the app's origin to that backend's CORS list.

**Errors.** `request()` throws `ApiError{status, detail}` and reads both FastAPI detail shapes:
a string, or a list of validation errors. An HTML error page (proxy error, unhandled 500) becomes
an `ApiError` rather than a JSON parse crash. Components map status codes to messages:

| Status | Meaning in this app |
|---|---|
| 409 | Stale review: the dataset changed underneath the reviewer |
| 422 | The data can't support the request (unqueryable dataset, no dashboard possible) |
| 429 | This project's LLM rate-limit bucket (Phase 7) |
| 503 | The AI provider is unavailable: quota, timeout, 5xx, or rejected request. The detail is shown verbatim |

## 5. Auth

- The access and refresh tokens live in `localStorage` (`auth/tokens.ts`). On load, `AuthProvider`
  calls `GET /auth/me`. `RequireAuth` shows a loading state while that runs, so a returning user's
  deep link isn't bounced to `/login`.
- **Refresh on 401 is single-flighted.** The backend rotates refresh tokens and revokes the one
  presented. If two concurrent 401s each tried to refresh, the second would present a revoked
  token and log the user out. `refreshAccessToken()` shares one in-flight promise.
- `NO_REFRESH_PATHS` is an exact set: login, register, refresh, logout. It is deliberately not a
  `/auth/` prefix check, which silently excluded `/auth/me`, the one call made on every load.
- A network failure during refresh keeps the tokens. Only a rejected refresh clears them.
- Roles: `hasAtLeastRole` mirrors `auth/rbac.py`. It only decides which controls the UI offers
  (upload and review need Analyst+). Every route re-checks on the server.

## 6. Data flow on the project page

```
upload (Analyst+) ─POST schema/discover─▶ 202 {job_id}
      └─ useDiscoveryJob polls GET schema/jobs/{id} until done|failed
         (a "still running" note after 60 s; there's no worker watchdog, see CLAUDE.md §6 Phase 7)
            └─ done ⇒ invalidate ['datasets', projectId]

GET /projects/{id}/datasets (newest first) ─▶ activeDataset = [0]
      ├─ SemanticModelView     key=activeDataset.id
      ├─ DashboardSection      key=dashboard-{id}
      └─ ChatPanel             key=chat-{id}
```

**Dataset versions are immutable.** A re-upload or a saved mapping review creates a new `Dataset`
row. The backend's result cache relies on this, and so does the UI. The three sections are keyed
by dataset id, so saving a review remounts them: staged decisions, a generated dashboard, and chat
answers never carry over to a version they weren't computed for. Sibling keys must differ; M6
caught the same bare id on two siblings.

**Mapping review.** Fields have a status of `auto`, `needs_confirmation`, `confirmed`, or
`rejected`. The engine computes only on `auto` and `confirmed` fields, via
`SemanticModel.trusted()` on the backend, and the review notice says so. Decisions are staged
locally and sent as one `POST …/mapping-decisions`, which returns a new version. A 409 means
someone else saved first.

**Dashboards are ephemeral** in v1: there's no dashboards table. Generation is a mutation, not a
query, so it never runs on mount or focus and never spends an LLM call unasked. Regenerating the
same dataset version is a result-cache hit.

**Chat is stateless.** Each question is independent, and the backend keeps no conversation.

## 7. Deterministic dashboard renderer

The LLM output is a `DashboardSpec`. By the time it reaches the browser it has been validated and
hydrated with engine results (`HydratedDashboard`). The UI maps component type to a React
component:

```ts
const RENDERERS: Record<ComponentType, Renderer> = {
  kpi: KpiRenderer, bar_chart: BarRenderer, pie_chart: PieRenderer, table: TableRenderer,
  line_chart: UnsupportedRenderer,   // no time-bucketing primitive in the engine yet
}
```

An unknown type at runtime falls back to `UnsupportedRenderer`, a visible refusal rather than a
crash. No LLM-generated code or markup is ever executed.

Every component is drawn inside `ComponentFrame`, which gives it:

- **A table view** of every chart, available through a toggle, so colour is never the only way to
  read the values.
- **Caveats** ("Can't be interpreted from this data") whenever the engine attached them. For
  example, `repeat_purchase_rate` on data where no customer has more than one order.
- **The planner's rationale**, labelled as a planner note.
- **"How this was computed":** row count, execution time, and the exact SQL that ran.

Chart rules, from the dataviz guidelines:

- Nominal bars use a single hue.
- Pies show at most 6 segments (top 5 plus "Other"). If the backend flags a pie's result as
  truncated, it falls back to a bar or table, because shares of an incomplete total would be wrong.
- The categorical palette is validated.
- When `result.truncated` is set, a grouped result shows a "top N" note.

**Number formatting** (`format.ts`) follows `MetricResult.format` (`money`, `count`, `percent`,
`number`), which the backend stamps from the metric registry:

- `formatExact` is used in tables, tooltips, and under a KPI headline.
- `formatHeadline` compacts values of 10,000 and above ("16M").
- No currency symbol is ever shown: nothing in an upload says which currency it's in.

## 8. Chat UI

Each answer card shows, in order:

1. **The verified result.** A scalar, a grouped table, or a before/after delta table, all
   formatted from the result itself.
2. **Caveats**, if any. A caveated answer shows only the caveat. Its explanation text is a
   deterministic backend restatement, not LLM prose, so it isn't repeated.
3. **The AI explanation**, labelled as such.
4. **"Interpreted as"**: metric, dimension, and the date periods read off the validated
   `answer.queries`, never off the question's wording. For example: `Apr 1, 2018 – Jun 30, 2018 vs
   Jan 1, 2018 – Mar 31, 2018 · data through <anchor date>`. This makes a wrong period visible to
   the user; M6 found one this way (§9).
5. **"How this was computed"**: the SQL.

A refusal shows the deterministic reason instead ("Can't answer this from the data: …").

## 9. End-to-end verification (M6)

`e2e/olist-journey.spec.ts` runs the whole product through the browser as a brand-new user:

1. Sign up, create an org, create a project.
2. Upload five real Olist CSVs (~60 MB) and wait for real schema discovery.
3. Review mappings as an analyst would, then save a new version. Discovery is LLM-assisted and
   varies between runs, so the review is a **policy**, not a fixed click script: reject
   known-wrong mappings (non-purchase dates as `transaction_date`, `freight_value` as revenue,
   `seller_id` as customer), confirm known-correct ones, and reject anything else unverified.
   The test then reloads and checks the decisions persisted.
4. Generate a dashboard. Every revenue, orders, or AOV KPI must match ground truth.
5. Ask chat questions. The answers must match ground truth, the periods shown must be correct, and
   "website conversion rate" must be refused.
6. No page errors or console errors occurred during the whole journey.

The ground truth was computed independently with hand-written DuckDB SQL over the raw CSVs, never
read back from InsightFlow:

| Check | Value |
|---|---|
| Revenue, all time (`SUM(payment_value)`) | 16,008,872.12 |
| Orders | 99,441 |
| AOV | 160.99 |
| Revenue, last quarter (Q2 2018; the sparse Sep/Oct 2018 tail isn't used as the anchor) | 3,338,648.13 |
| Revenue growth, Q2 vs Q1 2018 (Q1 = 3,267,119.64) | +2.19% |

**Running it.** The spec needs the real stack; nothing is mocked:

```bash
docker compose up -d postgres redis minio          # repo root
cd backend && uv run uvicorn insightflow_backend.main:app --port 8000   # needs GROQ_API_KEY
cd backend && uv run python -m insightflow_backend.worker                # discovery worker
cd frontend && npm run dev
cd frontend && npm run test:e2e      # ~2-3 min; OLIST_DIR=… if the CSVs aren't at ../Brazillian_Ecom_dataset
```

The spec uses a fresh account each run, so it can be re-run against the same dev database.

### Real findings from M6

1. **Growth compared the wrong period.** "Last quarter vs the quarter before" compared Apr 1–Jun 30
   with **Dec 31–Mar 31** and reported +1.87% instead of +2.19%. POC 4's resolver used an
   equal-day-count window, and Q2 has 91 days while Q1 has 90. For "last month" in March, the
   comparison window would have been Jan 29/30–Feb 28.

   *Fix:* complete calendar periods (`LAST_MONTH`, `LAST_QUARTER`, `LAST_YEAR`) now compare with
   the preceding calendar period. To-date periods keep the equal-length rule. Regression tests
   cover quarter, month, year-boundary, and leap-year cases. `COMPUTATION_VERSION` was bumped to
   11 so cached answers computed the old way are never served. POC 4's `hld.md` and
   `class_diagram.md` are updated.
2. **Duplicate React keys on the project page.** The dashboard and chat sections both used the bare
   dataset id as their key. React warned on every render with two dataset versions, and duplicate
   keys can skip a remount. *Fix:* the keys are now prefixed.
3. **Sign-up returned a raw 500 during a Redis stall.** The auth rate limiter calls Redis on
   `/auth/register`. A multi-second stall (a plain `PING` from a separate client also timed out)
   became an unhandled `redis.TimeoutError`. It was transient and not fixed here. Whether rate
   limiting should fail open or closed when Redis is unavailable is a Phase 10 hardening decision.

### Findings after M6, from real use

4. **Chat failed entirely on a dataset without a confirmed date column.** A 4-file Olist upload (no
   orders file; its only date column still awaiting review) returned a 422 on every question, even
   "total revenue". *Fix:* chat builds without date bounds and deterministically refuses only
   questions that need a period, saying a date column needs confirming.
5. **The explanation LLM did arithmetic.** "Total customers in AL, RN and CE" returned every region's
   count (the AST can't filter to values), and the explanation added three of them up: "2234". Each
   count was right, and so was the sum, but the engine never computed it. *Fix, in two structural
   parts (a prompt instruction alone isn't a guardrail):*
   - The planner extracts `QuestionIntent.dimension_values`. When it is non-empty, the pipeline
     refuses deterministically and suggests the breakdown that shows each value.
   - `InsightGenerator` checks every number in the explanation against the numbers it was shown,
     allowing for rounding ("16.0 million", "2.2%"). An explanation containing any other number is
     replaced with a notice that it was withheld. Verified on real Groq across money, counts,
     growth and a 20-row breakdown with no false positives.

   **Superseded by real filtering (below):** the refusal was a stopgap until the engine could filter.

   Follow-ups: the check now lives in insightflow_core (`validation/number_grounding.py`) and also
   covers POC 3's dashboard narrative (withheld) and component rationales (blanked). Its growth
   signals now reach the planner with four decimals, since "-0.10" couldn't ground an accurate
   "10.05%". A value-limited refusal names the dataset's real dimensions when the planner didn't
   pick one ("customers by category or region"). Checked against two real dashboards: every cited
   number was grounded and nothing was withheld.
6. **A discovery job that failed after a page reload was never reported.** The job id lived only in
   page state, and a Groq quota 429 during the M6 rerun left the page showing "No data yet". *Fix:*
   `GET /projects/{id}/schema/jobs` (newest first, `created_at` on `JobOut`). The upload panel resumes
   the latest job if it's still running, or shows its failure (with Dismiss) if it's newer than the
   project's newest dataset. The E2E test now reloads mid-discovery.

### Filtering by values

`AnalyticalQuery.filters` is a list of `ValueFilter{dimension, values}`. It is supported for every metric kind
except `having_ratio`, which is refused explicitly so a filter is never silently ignored.

- **Compilation.** Values are bound parameters, compared as trimmed, lower-cased text, so a numeric column
  such as a postcode still matches `"12209"`. A dimension on a related table is reached through a
  single-hop semi-join, the same construct as time filters, which can't double-count: the test fixture
  includes installment payments. A filter two relationships away is refused, as GROUP BY is.
- **Ratios and growth.** Both sides of a ratio are filtered, so "AOV in SP" is SP revenue over SP orders.
  Growth compares the filtered population in both periods.
- **Chat.** The planner extracts `dimension_values` and guesses `filter_dimension`. The pipeline confirms
  every value against the data (`match_filter_values`), trying the planner's dimension first and then the
  other dimensions. A value found nowhere is refused ("There's no category called "electronics" in this
  data (searched the "product_category_name" column)"). Values found under two dimensions are refused as
  ambiguous. The filter that actually ran, spelled as in the data, is shown in "Interpreted as" and passed
  to the explanation.
- **Verified with real Groq on Olist:** "total customers in AL, RN and CE" returns 2,234; SP returns 41,746;
  RJ and MG return 12,852 and 11,635. All match DuckDB exactly.
- **Canonical names map to one column per dataset.** On the 5-file project, the mapping review confirmed
  `customer_city` as region and rejected `customer_state`, so "SP" is correctly not found there. The
  refusal names the column searched.

### Findings from the real-world test datasets

Test datasets live in `test_datasets/` (git-ignored): jaffle_shop, Northwind, Superstore, UCI Online Retail II,
and (added for the ten-dataset survey below) Chinook, Maven Pizza Place Sales, ClassicModels
`sales_data_sample`, Kaggle supermarket sales, and BikeStores.

7. **Superstore: a raw 500 on every dashboard and question.** The downloaded CSV had Superstore's "People"
   and "Returns" sheets appended below the orders (a header change at line 9,996). pandas-based discovery
   padded the short rows and succeeded; DuckDB refused the file at query time.
   - *Fix:* uploads are checked with the engine's own CSV reader before discovery, and fail with "line 9996
     has 2 columns where the header has 21 ("Person,Region") ... split it into separate CSVs".
   - Writing the test exposed a quieter failure: with the ragged rows inside DuckDB's sniffing sample, it
     reads the whole file as one text column without any error. That's now rejected too.
   - A DuckDB read error at query time, from a dataset uploaded before the check, returns a 422 naming the
     file and line.
   - The check also caught a Northwind copy with unquoted commas in addresses. It's kept as
     `test_datasets/northwind_malformed`, with a valid copy in its place.
8. **Chat dates were ISO-only.** `infer_date_bounds` used `datetime.fromisoformat`, so Superstore's
   `11/8/2017` would have broken chat while dashboards worked. It now reads through DuckDB, like the
   engine. A column that doesn't parse as dates disables period questions with a reason instead of
   breaking chat.
9. **Raw floats in explanations.** Grouped rows reached the explanation prompt unformatted ("836154.0329999966").
   They are now formatted by the registry's display format, like scalar values.

Split Superstore (orders, people, returns), verified with real Groq against DuckDB: revenue 2,297,200.86,
5,009 orders, Q3 2018 revenue 196,251.96, growth +46.7% versus Q2, and category totals all match.

Still open from this run, not fixed:
- **`region` refused.** It maps a column on both the orders file and the people file, and a canonical field
  on two entities is treated as ambiguous, so "revenue by region" is refused.
- **A false relationship.** Discovery linked `superstore_orders.Customer Name` to `superstore_people.Person`
  (a regional manager).

### The ten-dataset survey

`e2e/dataset-journeys.spec.ts` walks ten datasets through the whole UI — signup, org, project, upload, real
schema discovery, an analyst's mapping review, a dashboard, and five questions each — and records what
happened per dataset instead of stopping at the first failure. It is a survey, not a regression check;
`olist-journey.spec.ts` remains the sharp one. Ground truth is hand-written DuckDB over the raw CSVs
(`e2e/ground-truth.py`, results in `e2e/ground-truth.json`), never read back from this system. Output lands in
`frontend/e2e-report/` (JSON plus full-page screenshots), which is git-ignored.

**What held.** 37 of 37 reported numbers matched ground truth exactly — every dashboard KPI and every chat
answer across nine datasets (the tenth is a deliberately malformed upload). No number was wrong anywhere, and
every metric a dataset genuinely could not express was refused rather than approximated. The
LLM-understands/engine-calculates split did the job it was built for. 1.07 M rows and 94 MB
(`online_retail_ii`) uploaded and discovered in 13 s, with queries at 3–9 s.

Everything below is a coverage, diagnosis or presentation failure — not an arithmetic one.

10. **`pizza_place_sales`: the product returns nothing at all.** Dashboard `422`, and all five questions
    refused, on a clean four-table schema with 21,350 orders and a real `category` column. The `orders`
    measure reads `order_id`, which is on both `orders` and `order_details`; its population hint
    `customer_id` does not exist anywhere in this dataset, so it cannot narrow the pair and binding refuses.
    A dataset with no customer identifier therefore loses its *order count* too.
    - The message the user sees is also untrue: the planner reports "No metric for counting orders is
      available in the listed metrics." The metric is registered; the binding failed. The planner is handed a
      metric list that has already had the unbindable ones removed, so it can only describe the symptom.
    - *Fix shape:* when the hint field is absent from the whole model it cannot discriminate, so fall back
      rather than refuse — the entity where the field is unique per row is the header table
      (`orders.order_id` is 21,350 distinct over 21,350 rows; `order_details.order_id` is not). Refusal is
      right only when the candidates would give different answers.
11. **A CSV that is not UTF-8 is rejected with a message that never says so.** Two of the five newly added
    datasets shipped as Windows-1252 — `sales_data_sample.csv` (byte `0x84`, line 29, a Swedish address) and
    `pizza_types.csv` (byte `0x91`, line 17, "'Nduja Salami"). `check_source_readable` reports
    `"…" can't be read: Invalid Input Error: CSV Error on Line: 29`, which reads like a ragged-row problem and
    gives the uploader nothing to act on. Excel on Windows writes cp1252 by default, so this will be common.
    Originals kept at `test_datasets/_cp1252_originals/`; working copies were transcoded to continue the run.
    - *Fix shape:* the cheap half is `describe_read_error` recognising the decode failure and naming it
      ("not UTF-8 — byte 0x84 at line 29; re-save as UTF-8"). The real fix is sniffing the encoding at upload
      and threading it into `read_csv_sql`, which every reader shares.
12. **A `m/d/YYYY H:MM` timestamp disables every time-based feature.** `classic_models`' `ORDERDATE` is
    `2/24/2003 0:00`. DuckDB's sniffer leaves it `VARCHAR`, so `CAST(… AS DATE)` in `infer_date_bounds` raises
    and chat refuses all period questions ("the ORDERDATE column doesn't read as dates (it mixes formats or
    holds text)") while all-time totals keep working. The format is perfectly consistent — the message's guess
    is wrong. Date-only US formats (`11/8/2017`, `1/5/2019`) sniff as `DATE` and are fine; adding a time
    component is what breaks it. *Fix:* `try_strptime` over a short format list, or pass `timestampformat`.
13. **`anchor_date`'s sparse-tail trim fails when the tail is gradual.** `bikestores` really ends in April 2018
    (125 orders); after it come 1, 4, 2, 1, 1, 2, 1 orders per month through December. `anchor_date` returned
    2018-12-28 — untrimmed. The rule compares the last month against the median of the months *immediately*
    before it, and those are themselves tail: median of `[1,4,2,1,1,2]` is 1.5, so December's 1 clears the 10 %
    bar on the first iteration and the loop breaks. Consequence: "last quarter" is a quarter holding 4 orders,
    and the dashboard narrative reported "Orders and customers have each declined by 33%" for a business that
    was flat (2018 H1 ≈ 281 orders vs 2017 H1 ≈ 351). This is the same failure Olist's two-month tail motivated
    the rule for; the rule just doesn't survive a seven-month one.
    - *Fix shape:* judge each candidate month against a baseline fixed *before* trimming starts (the median of
      the whole series, or of the months preceding the first dropped month), rather than against the
      already-degraded window that walks backwards with it.
14. **Five of ten datasets have no summable revenue column, so revenue, AOV and revenue growth are simply
    absent.** `pizza_place_sales` (quantity × price), `bikestores` (list_price × quantity × (1−discount)),
    `northwind` (same shape), `online_retail_ii` (Quantity × Price) — and `superstore`/`chinook`/`classic_models`/
    `supermarket_sales` only work because someone pre-computed a line total. The refusals are honest and
    correctly worded, but a line-item schema is the most common e-commerce shape there is. This is the
    `metrics_catalog.md` B2/B3 work (a measure over an expression, not a column) and it is the single
    largest gap the survey found.
15. **A canonical dimension mapped on two entities is refused with a message saying it doesn't exist.**
    `superstore` (`superstore_orders.Region` 98 % + `superstore_people.Region` 95 %, both auto) and
    `bikestores` (`customers.state` + `stores.state`, both auto). Chat answers "no region dimension is
    available", which is false — there are two. Finding 15's superstore case was already known; `bikestores`
    shows it is not a Superstore quirk. Nothing in the review UI marks the clash or offers to resolve it, and
    on `superstore` a user who reviews nothing at all lands here by default.
16. **Discovery maps on column names, not on values.** `northwind`'s `customers.region` — 60 of 91 rows NULL,
    values mixing `WA`, `Québec`, `Isle of Wight`, `Co. Cork` — mapped to `region` at 90 % and auto-accepted,
    while `customers.country` (21 clean values) scored 20 % and was left unmapped entirely. The dataset ends up
    with no usable region dimension. `chinook`'s `Country` mapped at 90 % on the same field, so the signal
    exists; the name match just outranks it.
17. **Key columns map to `category` at high confidence.** `categories.categoryID → category` at 95 % (beating
    `categoryName` at 90 %, which makes the numeric ID the canonical category column for that dataset),
    `pizzas.pizza_type_id → category` at 90 %, `tracks.GenreId → category` at 85 % — all auto-accepted, none
    shown to a reviewer. The same `*_id → category` shape recurred on three unrelated datasets.
18. **False relationships between surrogate keys, with no way to reject one.** `jaffle_shop` produced
    `raw_customers.id → raw_payments.id` at 95 % and `raw_orders.id → raw_payments.id` at 100 % — two of its
    four relationships are small-integer range overlaps between unrelated tables. `superstore`'s
    `Customer Name → Person` link is back at 89 %. The review UI covers fields only: a relationship can be
    read but not confirmed, rejected or edited, so a wrong join has no path out.

**Dashboard quality.** Every number on these dashboards is right; what is on them often isn't worth showing.

19. **`orders_per_customer` renders as a KPI reading "99".** On `jaffle_shop` it appears under the heading
    "Orders per customer" showing the total order count (99 orders across 62 customers — the real figure is
    1.6). It also surfaced as a table on `northwind` and `online_retail_ii`. It is a helper measure for
    `repeat_purchase_rate`'s GROUP BY and should not be selectable as a component.
20. **Duplicate components.** `northwind` rendered "Orders by customer name" (bar, top 20) and "Orders per
    customer by customer name" (table, top 20) side by side with identical rows — Save-a-lot Markets 31, Ernst
    Handel 30, QUICK-Stop 28. Two of its three non-KPI components were the same query. `chinook` rendered
    "Revenue by region" and "Orders by region" as two near-identical 20-bar charts.
21. **`product_name` gets picked as a part-to-whole dimension.** `superstore`'s "Orders by product name" pie
    degraded to the truncation notice and a list of five products (48, 46, 46, 20, 19 of 5,009 orders, out of
    1,850 products); `online_retail_ii`'s "Customers by product name" fetched 1,000 truncated rows. The
    truncation guard behaves correctly — the component should never have been planned.
22. **Raw floats in planner prose.** "Revenue is highest in Technology (836154.0329999966)", "total revenue of
    2297200.8603", "growth 0.4472". Finding 9 formatted grouped rows in chat explanations; dashboard narratives
    and component rationales still carry the unformatted values, and they are the most prominent text on the page.
23. **Speculative claims, on every dataset with a narrative.** "may mask reliance on new acquisition"
    (chinook), "growth is driven by existing customers ordering more frequently rather than by acquiring new
    customers" (northwind), "growth is currently driven by a saturated repeat customer base" (superstore). The
    numbers are grounded; the causal stories attached to them are not. This is §10's known limitation, now
    observed on five of six narratives rather than argued about in the abstract.
24. **A withheld narrative shows an apology where the summary should be.** `online_retail_ii`: "The planner's
    summary was withheld because it stated a number the analytics engine didn't compute." The guard is right to
    fire; the top of the dashboard is the wrong place to explain it. Retry once, then omit quietly.
25. **The page is one long scroll with the least useful thing first.** The mapping table is always above the
    dashboard and the chat box, so on `chinook` reaching either means scrolling past 42 mapping rows, and
    rejecting a mapping makes the table *longer* (rejected rows stay full height). Unmapped columns — roughly
    half the rows, all at 20 % — are never collapsed. Three tabs, or collapsing the model once reviewed, would
    fix the whole class.
26. **The review can only accept or reject, never correct.** Where discovery misses a column (`northwind`'s
    `country`, finding 16) or picks the wrong one of two (finding 17), the reviewer has no way to say what the
    column actually means, so the dataset stays broken no matter how carefully it is reviewed.
27. **A provider 503 loses the question.** Groq returned "usage limit has been reached" mid-session on
    `classic_models`; the turn failed with a red box and no retry. One backoff-and-retry would have hidden it.
28. **The discovery job prints the provider's raw error, including the Groq organization id.** `main.py`
    maps `groq.RateLimitError` to a clean 503 ("The AI provider's usage limit has been reached…") — and that
    is what chat showed in finding 27. But those are *FastAPI exception handlers*, and the discovery job runs
    in the RQ worker, outside the ASGI app: `jobs.py` catches bare `Exception` and stores `str(e)` on the `Job`
    row, which `DatasetUpload` renders verbatim. Running out of Groq's daily token budget therefore puts this
    in front of an Analyst who uploaded a CSV:
    `Discovery failed — Error code: 429 - {'error': {'message': 'Rate limit reached for model
    openai/gpt-oss-120b in organization org_01m01wp9peeyh9jzteppjfvt58 service tier on_demand on tokens per
    day (TPD): Limit 200000, Used 200000 … Upgrade to Dev Tier today at https://console.groq.com/settings/billing'`
    — an internal provider account identifier and a billing upsell, in a tenant-facing UI.
    - Found by the Olist rerun at the end of this survey, which exhausted the day's token quota and so could
      not complete; the failure is environmental, but the way it was displayed is not.
    - *Fix shape:* the mapping from provider exception to user-facing sentence belongs somewhere both callers
      share, not in an ASGI handler — `jobs.py` should classify the exception the same way before writing
      `Job.error`, and a provider quota failure should arguably leave the job retryable rather than `failed`.

### What the survey's findings changed

Fixed, each with a regression test naming the dataset that found it. `COMPUTATION_VERSION` went to 17:
four of these change the result for an unchanged dataset, so the result cache must not keep serving the
old one.

- **10 — the binding refusal that emptied `pizza_place_sales`.** `measure_binding._header_entity`: when a
  population hint names a field *no* entity in the dataset has, it is discriminating nothing, so the
  relationships decide instead — the candidate every other candidate points at over the same field is the
  header table. A hint that exists and merely fails to narrow still refuses, because then the candidates
  really do disagree about grain (the Olist case that motivated hints keeps its old behaviour, and its
  test still asserts the refusal).
- **11 — the encoding message.** `check_source_readable` now scans for the first byte that isn't valid
  UTF-8 before falling back to DuckDB's wording, and names it: *"sales_data_sample.csv" isn't UTF-8 text:
  the byte 0x84 on line 29 is not valid UTF-8. Spreadsheets often save as Windows-1252 instead…*. The scan
  streams, so a character straddling a 1 MiB read boundary isn't mistaken for corruption. Reading cp1252
  files directly is still not supported — this only makes the refusal actionable.
- **12 — `m/d/Y H:MM` dates.** New `compilation/date_expr.date_expression`, used by both the compiler's
  time-filter predicate and `infer_date_bounds`, so the window a question reports and the window its
  `WHERE` clause selects cannot disagree. The `try_strptime` fallback is reached only when the plain cast
  yields NULL, so every column DuckDB already types as a date reads exactly as before. ClassicModels now
  parses 2,823 of 2,823 rows, 2003-01-06 to 2005-05-31. Because the fallback yields NULL where the old
  cast raised, `infer_date_bounds` now checks explicitly that *every* non-null value parsed, keeping the
  old strictness rather than silently dropping rows from time filters.
- **13 — the sparse-tail anchor.** `_anchor_from_counts` measures a candidate month against the median of
  every month still kept before it, not the six immediately preceding ones, which are themselves tail on
  any tail longer than a month or two. BikeStores now anchors in April 2018 instead of 2018-12-28.
- **19 — helper measures on dashboards.** `Measure.planner_selectable` (default True, False on
  `orders_per_customer` in all four registries), honoured by `resolvable_metric_names(..., planner_only=True)`
  in both planner contexts. Validators deliberately don't pass it: a deterministic query naming the measure
  is still legal, it just isn't something an LLM should pick. This also removes finding 20's northwind
  duplicate, which was `orders by customer_name` beside `orders_per_customer by customer_name`.
- **28 — the provider error leak.** New `backend/llm_errors.py` holds the exception→sentence mapping;
  `main.py`'s handlers and `jobs.py` both call it, so the two can't drift. Confirmed against a real Groq
  429: the `Job` row now reads *"The AI provider's usage limit has been reached. Try again in a few
  minutes. Re-upload to try again."* instead of the provider's body.

Still open: **15** (an ambiguous dimension is still reported as one that doesn't exist — the honest fix is
deterministic, since a prompt instruction is not a guardrail, and the review UI has nowhere to resolve the
clash), **14** (revenue as an expression — the B2/B3 engine work), **16–18**, **21–26**. **27** was fixed
after the third run; see the end of this section for why it went first.

### The second run, against the fixed code

All ten journeys again, real Groq, same ground truth. **38 of 38 numbers matched exactly**, plus
ClassicModels' revenue growth at −0.4679344331 against a hand-computed −0.4679344331 — a figure the dataset
could not produce at all before. Still no wrong number anywhere.

What changed, dataset by dataset:

- **`pizza_place_sales` answers questions.** "How many orders were there in total?" returns 21,350, matching
  DuckDB, where every question was refused before. Its dashboard still failed, but on something new —
  finding 29 below.
- **`classic_models` has periods.** The growth question is answered from a real window (Q1 2005 vs Q4 2004),
  and the dashboard narrative now carries computed growth (+0.12% revenue, −5.71% orders) instead of nothing.
- **`bikestores` anchors on 2018-04-30**, not 2018-12-28, and its narrative reports growth rather than the
  33% decline it read off four stray orders.
- **No `orders_per_customer` component on any dataset.** That also removed northwind's duplicate pair, and
  Superstore's part-to-whole moved from "Orders by product name" (1,000 truncated rows) to "Orders by
  category" (3). A `customers by product_name` table still appears, so finding 21 is narrowed, not closed.
- **Finding 28 confirmed in the wild:** both runs hit a real Groq 429 and the `Job` row read the clean
  sentence, with no organization id and no billing link.

29. **A dataset with one usable metric gets no dashboard at all.** `pizza_place_sales` resolves exactly one
    metric (`orders`) and no dimension the engine can join to it in a single hop — `category` sits two hops
    away, through `order_details` and `pizzas`. Asked for `DASHBOARD_MIN_COMPONENTS` (3), the planner padded
    the spec by repeating the same KPI, and the validator refused the whole thing: *invalid dashboard spec:
    ['metric "orders" appears as more than one KPI component']*. The minimum is unsatisfiable on this
    dataset, so no spec could ever have passed.
    - *Fixed* two ways, both deterministic. `_drop_duplicate_kpis` repairs the spec before validation — a
      repeated KPI says nothing twice and is never a reason to refuse everything, while the same metric by
      two different dimensions is two real views and is kept. And `PlannerContext.component_ceiling` (one KPI
      per resolvable metric, plus one per joinable metric/dimension pair) lowers the floor to what the
      dataset can actually supply; it only ever lowers it.
    - Unit-tested, but **not yet confirmed end to end**: the second run exhausted the day's Groq token quota
      before this fix existed, so the next survey run is what proves `pizza_place_sales` renders a dashboard.
- Finding **27** (no retry on a provider 429/503) bit twice more during this run — one chat question lost on
  `classic_models` and one on `superstore` — and is still unfixed.

### The third run: finding 29 confirmed, and finding 27 dominating

With finding 29's fix in, `pizza_place_sales` renders a dashboard — one honest KPI reading 21,350 orders,
matching DuckDB, with no findings at all. That journey has now gone: no dashboard and every question refused
→ chat working but the dashboard 422 → a dashboard, the order count right, and every refusal honest. The
canonical `olist-journey.spec.ts` passes clean on the same code, and the backend suite is 142/142 with both
real-Groq integration tests included. 31 numbers matched ground truth this round; none was wrong.

Seven of the ten journeys were verified on the fixed code. The other three were cut off by Groq's daily
token ceiling, which three separate keys hit over the course of the survey — the runs themselves are what
exhaust it.

That cost is worth naming precisely, because it is **finding 27 at full strength**. In one pass, six of ten
journeys were derailed by transient provider failures: four datasets lost schema discovery outright and
`bikestores` lost four of its five chat questions, every one to a single 429 or "didn't respond" with no
retry behind it. Re-running the same six immediately afterwards, unchanged, passed three of them. Nothing
about those datasets was wrong; the platform simply has no backoff, so one unlucky call loses a whole
upload. Finding 27 should be read as the most frequently-hit defect in the survey, not a footnote.

**27 — fixed.** `backend/llm_retry.py`'s `RetryingLLMClient` wraps all three provider clients at the wiring
layer — schema discovery, dashboard and chat — rather than inside any one of the four vendored
`GroqLLMClient` copies. Three attempts, exponential backoff with jitter, capped at 8 seconds a wait.

Two things it deliberately does not do, both of which the survey argued for:

- **It does not wait out a daily quota.** Groq answers an exhausted per-day token budget with the same 429
  class it uses for a per-minute burst, but a `retry-after` in minutes cannot succeed inside a request. A
  hint longer than the sleep cap means "don't bother", and the caller gets `llm_errors`' clean sentence
  immediately. Confirmed live while this was being written: a 429 carrying `try again in 1m57s` went
  through `RetryingLLMClient` and failed on the first attempt, with no sleep.
- **It does not retry a request the provider rejected on its merits.** `llm_errors.is_transient` is the one
  definition of what is worth repeating; a 413 for an over-long prompt will be rejected identically every
  time.

`llm_errors.py` now earns its keep twice over — it already knew which failures are transient for finding
28's message, and that same predicate decides what to retry here.

One button under the semantic model, shown to Analysts and above only, matching the route's own gate:
it downloads a Power BI project the reader opens in Power BI Desktop. Nothing Power BI renders appears
in this app — there are no Power BI licences, so the Service, embedding and the REST API are out
entirely (`backend/docs/phase9_powerbi.md` §1).

`client.ts` gained `downloadFile`, separate from `request` rather than a flag on it: `request`'s
contract is "read the body as text, parse JSON, turn non-2xx into an ApiError", and a body can only be
read once, so a Blob response needs its own path. It keeps the same refresh-on-401 retry.

The component states what the download gives up — caveats, the truncation flag, number grounding and
this project's roles all stop at the file — in the UI rather than only in the zip's README, because
that is where the decision to click is made.

## 10. Known limitations and deliberately deferred items

- **Speculative LLM prose.** Numbers in LLM text are now checked (finding 5), but claims aren't: a
  planner narrative can still say "growth is driven by higher spend per existing customer". A
  post-computation narrative step is the likely fix.
- **No ad-hoc query view.** `POST analytics/query` has no UI yet. It needs a capabilities route
  listing the metrics and dimensions available for the active dataset.
- **Line charts** are unsupported until the engine has time bucketing.
- **No persisted dashboards.** Dashboards are ephemeral in v1, and chat has no conversation memory.
- **Locale-dependent formatting.** Number formatting follows the browser locale. The E2E config
  pins `en-US` because it asserts on rendered text.
- **No worker watchdog.** A killed discovery worker leaves its job `running` forever, and the UI
  can only say it's taking longer than expected. `docker-compose.yml` has no restart policies.
- **`orders_per_customer`** is a helper measure but can surface as its own dashboard table.
- **The Power BI export has no E2E coverage** and its TMDL has not yet been opened in Desktop —
  the parity run in `backend/docs/phase9_powerbi.md` §6 is the outstanding check.
