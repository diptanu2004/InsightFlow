# POC 2 — High-Level Design

## Analytics Engine (InsightFlow)

> **Status:** Implemented and closed — this is the design POC 2 was originally built against.
> Left as written rather than rewritten after the fact (same practice POC 1's diagrams followed:
> "revised at closure ... to match the implementation as it actually stands, not just the
> original plan" — see `poc1_schema_discovery/README.md`'s "Roadmap" section), because the plan
> below held up well: every stage in the diagram exists in the shipped pipeline under the same
> name. Real deviations from this plan are called out explicitly in `class_diagram.md`'s
> "Decisions made during implementation" and its two "Findings" sections, not silently folded
> back into this file. This file's own "Open questions for implementation" section (bottom) is
> now annotated with how each was actually resolved.

## Scope decided so far

- **No LLM dependency.** POC 2's only input contract is a structured **AST** (analytical query).
  Natural-language handling belongs to POC 4; POC 2 proves the deterministic side works in
  isolation (architecture doc §2.1, §32.5).
- **DuckDB, in-process, no server.** The SQL compiler emits real SQL, executed against DuckDB
  reading the semantic-mapped source files directly — same embedding model as SQLite, but
  columnar/OLAP, so it's a close analogue of the eventual Postgres target (§11, §22) without
  standing up a database this early (§29 puts Postgres/multi-tenancy at Phase 6-7).
- **Shared substrate for two future callers.** POC 3's dashboard planner and POC 4's chat
  planner will both produce ASTs and hand them to this engine — POC 2 doesn't know or care which
  produced a given AST. Until POC 3/4 exist, hand-written fixture ASTs stand in for both.
- **Metric Registry is two-tier, not a flat list.** Tier 1 is a small set of composable
  primitives (registered *measures* — aggregatable fields — plus generic *operators*: ratio,
  growth-over-time, HAVING/threshold filters, ...). Tier 2 is a curated set of human-named
  metrics (Revenue, AOV, Repeat Purchase Rate, ...) defined as compositions of Tier 1 primitives.
  This is what lets the registry grow to cover real-world e-commerce metrics without turning
  into an ever-expanding flat list of hardcoded formulas — see "Metric Registry — two-tier
  structure" below.
- **Multi-hop joins are out of scope for POC 2.** Dimension resolution (`FieldResolver`) only
  ever performs a single join, e.g. `orders → products` for category, `orders → customers` for
  region. Flattening a normalized source schema into single-hop-clean entities (star schema, not
  snowflake) is treated as POC 1's modeling responsibility, not something POC 2's compiler
  compensates for at query time — see `class_diagram.md` for the full reasoning. Tracked as a v2
  platform backlog item under POC 1's area of responsibility, not POC 2's.
- **SQL is independently re-checked after compilation, not just validated before it.** The AST
  Validator checks the *input*; a separate Safety Checker re-verifies the *compiled SQL itself*
  before it ever reaches DuckDB — this is what architecture doc §11.2 already names as a distinct
  "SQL Validation" step between SQL generation and execution, which the first draft of this HLD
  had collapsed into AST validation alone. See the diagram and Notes below.

---

```mermaid
flowchart TD
    subgraph EXT["Future AST producers (outside POC 2)"]
        direction LR
        P3["POC 3: Dashboard Planner\n(fixed metric set, deterministic)"]
        P4["POC 4: NL Chatbot Planner\n(LLM, per-question)"]
    end

    FIX["POC 2 test fixtures\nhand-written ASTs standing in\nfor P3 / P4 until they exist"]
    P3 -. not built yet .-> AST
    P4 -. not built yet .-> AST
    FIX --> AST

    AST["Analytical Query / AST\n{operation, metric, aggregation,\ndimension, time_filter, sort, limit}"]

    SM["Semantic Model JSON\n(POC 1 output — entities, canonical fields,\nsource_column / source_file mappings)"]

    subgraph REG["1. Metric Registry (two-tier)"]
        direction TB
        T1["Tier 1: Measures + Operators\nmeasures: revenue, order_id, customer_id, ...\noperators: SUM, COUNT, COUNT DISTINCT, AVG,\nratio, growth-over-time, HAVING-threshold"]
        T2["Tier 2: Named metrics\nRevenue = SUM(order.revenue)\nAOV = Revenue / Orders\nRepeat Purchase Rate = HAVING(order_count >= 2)"]
        T1 --> T2
    end

    AST --> VALIDATE["2. AST Validator\n- metric resolves in Tier 1 or Tier 2?\n- dimension / field resolves in Semantic Model?\n- time_filter well-formed?\n- row-limit present / enforced?"]
    REG --> VALIDATE
    SM --> VALIDATE

    VALIDATE -- invalid --> REJECT["Rejected\nstructured error, no query built,\nnothing reaches the compiler"]
    VALIDATE -- valid --> COMPILE["3. SQL Compiler\nresolves canonical field -> source_file/source_column\nvia Semantic Model, emits DuckDB SQL\n(kept Postgres-portable on purpose)"]
    SM --> COMPILE

    COMPILE --> CHECK["3b. SQL Safety Checker\nre-parses the COMPILED SQL independently of the AST\n- single SELECT statement, no stacked statements\n- every table/column matches the Semantic Model allowlist\n- LIMIT present, within max_row_limit\n(architecture doc §11.2's explicit \"SQL Validation\" step;\ncatches compiler bugs, not just bad AST input)"]
    CHECK -- unsafe --> REJECT2["Rejected\ncompiled SQL failed independent check\n(signals a compiler bug -- logged distinctly from\nan AST-level rejection)"]

    DATA["Semantic-mapped source data\norders.csv, customers.csv, products.csv"] --> DUCK["DuckDB\nin-process, queries CSVs / DataFrames\ndirectly, no server"]
    CHECK -- safe --> DUCK
    DUCK --> EXEC["4. Query Executor\nruns compiled SQL, catches errors,\nenforces timeout + result row cap"]
    EXEC --> RESULT["5. MetricResult\ntyped, deterministic\nvalue(s) + query metadata"]

    RESULT --> EVAL["6. Evaluation Harness\nfixture ASTs + known-correct expected values\n-> numerical correctness, latency\n(same pattern as POC 1's ground-truth benchmark)"]

    style AST fill:#e8f0fe,stroke:#4285f4
    style T1 fill:#fef7e0,stroke:#f9ab00
    style T2 fill:#fef7e0,stroke:#f9ab00
    style VALIDATE fill:#fce8e6,stroke:#ea4335
    style COMPILE fill:#fce8e6,stroke:#ea4335
    style CHECK fill:#fce8e6,stroke:#ea4335
    style RESULT fill:#e6f4ea,stroke:#34a853
    style EVAL fill:#fef7e0,stroke:#f9ab00
    style REJECT fill:#fce8e6,stroke:#ea4335
    style REJECT2 fill:#fce8e6,stroke:#ea4335
```

---

## Metric Registry — two-tier structure

The registry doesn't try to pre-enumerate every metric a business might ever ask for — that
doesn't scale to real datasets. Instead it's a grammar, not a list:

- **Tier 1 — Measures.** Raw, aggregatable fields drawn from the Semantic Model (`revenue`,
  `order_id`, `customer_id`, ...), each with a default aggregation (`SUM`, `COUNT`,
  `COUNT DISTINCT`). Registering a new measure is cheap *if* the underlying field is already in
  the Semantic Model — POC 1's job, not POC 2's.
- **Tier 1 — Operators.** A small set of generic compositions that work over *any* registered
  measure: ratio-of-two-measures, growth/delta over two time windows, HAVING/threshold filters
  on grouped results, share-of-total, rank/top-N (the last one is already free — it's just the
  AST's existing `sort`/`limit` fields). Most of these map onto SQL primitives DuckDB and
  Postgres already provide natively (window functions, `PERCENTILE_CONT`, `STDDEV`, `CORR`) — the
  work is exposing them through the AST/validator, not writing new math.
- **Tier 2 — Named metrics.** Human-meaningful names (Revenue, AOV, Repeat Purchase Rate, ...)
  defined as fixed compositions of Tier 1 measures + operators. These are what dashboards and
  chat reference by name so the two never disagree with each other.

Adding a metric costs one of four things, in increasing order of effort: a pure Tier 2 entry
(composition of things that already exist); a new Tier 1 measure first (the field exists in the
Semantic Model, just isn't registered yet); a new Tier 1 operator (a primitive the compiler
doesn't emit yet, e.g. percentile or moving average); or a genuinely new metric family (cohort
curves, RFM, predictive CLV, forecasting, anomaly detection — multi-step or model-based, not a
single group-by aggregation, and not a registry row at all).

A full research pass classifying real-world e-commerce metrics (common and obscure) against this
framework lives in this folder as `metrics_catalog.md` (Category → Bucket → Metric, doubles as a
build-order map) and, as a flat reference table, in the Claude project under the same filename —
that's the backlog this registry grows into for v2 of the full platform. POC 2's actual initial
implementation scope is the small subset in `supported_metrics.md` (this folder).

---

## Notes

- **Why validation happens before compilation, not after.** An AST referencing an unregistered
  metric or an unmapped dimension should never reach the SQL compiler at all — this is the
  concrete mechanism behind §12's "never blindly execute" principle, scoped down to what's
  actually buildable pre-multi-tenancy: no tenant isolation or read-only DB credentials yet
  (those need Phase 6's auth layer), but "only registered, resolvable operations can become SQL"
  is a property of the AST/validator design itself and holds from day one.
- **The Metric Registry is the single source of truth**, referenced by the validator and the
  compiler now, and by POC 4's LLM planner later (§9.2 — "the LLM should reference registered
  metrics rather than inventing formulas"). Getting its shape right in POC 2 is what lets POC 4
  reuse it unchanged rather than redefining metrics.
- **Field resolution is two-hop (three, for Tier 2 metrics)**: an AST names a metric
  (`revenue`, or a named Tier 2 metric like `aov`); the Registry resolves that down to Tier 1
  measures + operators (`aov` → `revenue / orders`); and each Tier 1 measure resolves through the
  Semantic Model to where it physically lives (`orders.net_amt`, per POC 1's actual
  `semantic_model.json` shape). The compiler only ever touches source columns through this
  resolution — it never sees raw column names in the AST itself.
- **Why DuckDB over pandas-only:** computing metrics via dataframe ops instead of SQL would be
  simpler short-term, but it skips exercising the SQL compiler — arguably the riskiest piece
  architecturally, and the part that has to keep working when Postgres replaces DuckDB in
  Phase 6+. DuckDB forces real SQL generation now at near-zero setup cost.
- **Why the Safety Checker is a separate stage from the AST Validator, not just "more
  validation."** The AST Validator can only ever check what a well-behaved compiler *should*
  produce from valid input — it has no way to catch a bug in the compiler itself (e.g. a string
  formatting mistake that accidentally lets a raw value leak into an identifier position). The
  Checker re-parses the actual SQL text the compiler emitted and independently re-derives "is
  every table/column here one I recognize, is this really just one SELECT, is there really a
  LIMIT" — the same questions, asked of the output instead of the input. Two independent checks
  that both have to agree the query is safe is a materially different guarantee than one check
  trusted to be correct forever. See `class_diagram.md`'s parameterization-convention note for
  the identifier-vs-value rule this is enforcing on the compiler side in the first place.

## Open questions for implementation — resolved

- **Exact AST schema**: settled as `models/query.py`'s `AnalyticalQuery` — `time_filter` is
  always an explicit `start_date`/`end_date` (a calendar range, never a relative-range string or
  enum), and one AST names exactly one metric (multi-metric queries were decided out of scope,
  not deferred silently — a caller issues one `AnalyticalQuery` per metric).
- **Row-limit / timeout enforcement**: row-limit ended up enforced in three places, not one —
  `ASTValidator._validate_row_limit` rejects upfront, `SQLCompiler._apply_sort_limit` clamps the
  compiled `LIMIT` to `max_row_limit` regardless, and `QueryExecutor._enforce_row_cap` is a
  defensive backstop at execution time. **Timeout did not get the same treatment**: it's
  configurable (`QUERY_TIMEOUT_SECONDS`, `config.py`) and stored on `QueryExecutor`, but nothing
  actually enforces it — DuckDB's Python API has no first-class portable per-query wall-clock
  timeout, and building one was never done. Honestly flagged in `QueryExecutor`'s own code
  comment as future work, not silently dropped; carried forward in the closure backlog.
- **`MetricResult` shape**: one typed shape ended up used, per this section's own instinct —
  `value` (scalar) XOR `rows` (grouped), never both, enforced by a pydantic validator. What this
  question didn't anticipate: that invariant also currently forbids the *third* legitimate case —
  a scalar result that's genuinely undefined (e.g. `SUM` or a ratio's denominator over zero
  matching rows) — found during the closure sweep; see `class_diagram.md`'s closure-sweep
  findings for what was fixed there (SUM defaults to 0, the mathematically correct empty-sum
  identity) and what's still an open gap (a genuinely-undefined ratio still has no way to be
  represented without crashing).
- **Which Tier 1 operators first**: built in the order this section predicted — the three B1 base
  measures + dimension grouping, then growth-over-time (unblocking 3 metrics at once), then
  HAVING/threshold (unblocking the last one) — see `supported_metrics.md`'s build order, which
  this matches exactly.
- **SQL parser for the Safety Checker**: `sqlglot`, as this section's instinct suggested over
  hand-rolled regex matching — see `SQLSafetyChecker`.
