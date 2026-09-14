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

   Real filtering by values (so the engine computes such totals) is planned as its own piece of
   work: it's a new capability in the shared AST and SQL compiler.

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
