# Phase 9 — Power BI Integration

> Scope, design decisions, and every real finding. Companion to `hld.md`.

## 1. What Phase 9 is, and what it deliberately isn't

The architecture doc (§14) asks that the same semantic model feed Power BI as well as the native
React dashboards, and that Power BI never become the source of business definitions. Phase 9
delivers exactly that and nothing more: a **downloadable Power BI project**, generated from the
project's semantic model and metric registry, that a person opens in **Power BI Desktop**.

**Nothing is rendered inside InsightFlow.** No embedding, no Power BI Service, no workspaces, no
scheduled refresh, no REST-API report creation, no XMLA.

That is a licensing fact, not a scoping preference. Embedded Power BI requires the report to live
in a Service workspace first — you embed a *published* report by requesting an embed token against
it, which needs an Entra ID tenant, an app registration, a Pro/PPU service principal and capacity.
There is no file-based or local embed mode; a `.pbix` on disk cannot be embedded. This project has
Power BI Desktop and no licences, so the file *is* the integration.

The upside of the constraint: with no shared published report, a DAX/SQL divergence can't quietly
reach another tenant. It stays in one person's Desktop session.

## 2. Shape

```
Dataset (trusted semantic model + its CSVs)
        │
        ├─ powerbi/build.py   -> BIModel      (all decisions: types, cardinality, DAX, calendar)
        │     └─ powerbi/dax.py               (registry metric -> DAX, via the engine's own binding)
        │
        ├─ powerbi/tmdl.py    -> PBIP/TMDL text files   (formatting only)
        └─ powerbi/export.py  -> .zip (project + data/*.csv + README.md)
                │
                └─ routers/powerbi.py  GET /projects/{id}/powerbi/export
```

`powerbi/ir.py` holds the `BIModel` types. The split is the point: everything that could change a
number is decided in `build.py` against types this repo owns and is unit-tested with no Power BI
involved (`tests/test_powerbi_export.py`, 23 tests). `tmdl.py` is a dumb serializer, so a formatting
mistake there is cheap and changes no numbers.

## 3. The central risk, and what contains it

A DAX measure is a **second deterministic implementation** of a formula `insightflow_core` already
implements in SQL. The two can disagree silently. This is the one place Phase 9 strains §2's "LLMs
understand; deterministic systems calculate" — not because an LLM is involved (none is, anywhere in
the export) but because there are now two calculators.

Three things contain it:

1. **No metric is hand-translated.** `dax.measure_dax` calls `bind_metric` — the same call
   `SQLCompiler.compile` makes — so which entity a measure reads and which physical column it
   aggregates cannot drift from the engine, on any dataset. A metric the engine would refuse here is
   refused identically, by the same exception, and lands in the README's "not exported" list with the
   engine's own wording.
2. **Every translation states the SQL it mirrors**, in `dax.py`. Where DAX cannot match SQL, the
   difference is written down rather than discovered later.
3. **A parity run** (§6) is the milestone that actually verifies it.

Known, intended divergences:

| Case | SQL | DAX | Why |
|---|---|---|---|
| `SUM` over no rows | `COALESCE(SUM(c), 0)` | `COALESCE(SUM(c), 0)` | matched deliberately; bare DAX `SUM` returns BLANK |
| `COUNT(DISTINCT c)` | ignores NULL | `DISTINCTCOUNTNOBLANK` | DAX's `DISTINCTCOUNT` counts BLANK as a value — a silent off-by-one |
| cross-entity RATIO | two independent subqueries | filter context propagates through relationships | unfiltered they agree; under a slicer Power BI's answer is the more useful one and is not the engine's |
| `GROWTH` | two periods from the AST | current date-filter window vs. preceding equal-length window | a measure has no query to take periods from, so it uses POC 4's own `_preceding_equal_length_period` convention |

## 4. Decisions worth knowing

**Columns keep their physical names.** A real semantic model maps several physical columns onto one
canonical name within a single entity — real Olist puts `customer_city`, `customer_state` and
`customer_zip_code_prefix` all on `region`, and both `order_purchase_timestamp` and
`order_approved_at` on `transaction_date`. Renaming to canonical names would produce duplicate
column names and an invalid model. Physical names also match the views `QueryExecutor` creates, which
rename nothing, so a column means the same thing in Desktop as in the engine. The canonical mapping
and its confidence ride along as the column's description.

**Only trusted mappings leave the platform**, plus relationship join columns. `build_export_zip`
calls `.trusted()` itself rather than trusting its caller, matching `PipelineCache._computable_model`.
Join columns survive an unreviewed or rejected *meaning* because the physical foreign key is still
what joins the tables — the same allowance the SQL safety checker already makes.

**Cardinality is measured, not guessed.** POC 1's `Relationship` records no grain at all, and Power
BI requires one on every relationship. `build.py` counts distinct values per join column with the
engine's own reader and orients the relationship so the many side is `fromColumn`. Getting this
backwards is how a model silently fans a SUM out across the many side — the same failure
`_compile_grouped_base`'s `ROW_NUMBER` dedup exists to prevent in SQL.

**Cycles are broken deterministically.** Power BI allows one active path between two tables; POC 1's
output routinely has more. Relationships are taken highest-confidence first and any edge closing a
loop over already-connected tables is marked `isActive: false` with its reason (surfaced in the
README). On real Olist that deactivates the direct `order_items → payments` edge (0.99) in favour of
the two-hop path through `orders` (0.995) — rather than leaving Desktop to pick one.

**The calendar comes from the data, never the clock.** Its range is `infer_date_bounds`' own
`(min_date, anchor_date)`, resolved exactly the way chat resolves it (including `TIME_ENTITY`). The
sparse trailing tail `anchor_date` trims — real Olist's 20 stray orders after August 2018 — stays
outside the calendar. `CALENDAR(DATE(...), DATE(...))` uses literal bounds; `TODAY()` appears nowhere
in the generated model, and a test asserts that.

**A timestamp can't join a calendar.** A generated `InsightFlowDate` calculated column
(`DATE(YEAR(..), MONTH(..), DAY(..))`) is the date-only key; a direct relationship on the raw
timestamp would match almost nothing.

**Re-exports are byte-identical.** Lineage tags are `uuid5` of each object's own path rather than
`uuid4`, so an unchanged dataset produces an unchanged project and a person tracking it in git sees
real changes only.

**Analyst, not Viewer, and audit-logged despite being a GET.** Every other read route returns
numbers the engine computed, with caveats, truncation flags and number-grounding checks attached.
This one returns the raw rows, and once the zip is on a disk the project's roles no longer apply to
it. That makes it a bulk data-egress event, which is what an audit trail most needs to show.

**Not in `ResultCache`.** A dataset is immutable so the bytes would be stable, but the CSVs are the
bulk of them (real Olist is 22 MB) and Redis is not a file store. Rebuilding costs a few seconds of
local DuckDB profiling.

## 5. What the export cannot carry

Written into the README inside every zip, because a person opening the file in Desktop needs it
there rather than here:

- **Caveats.** `MetricResult.caveats` is a runtime protection; a measure in Desktop shows no warning.
  Where a caveat can apply, its *condition* is written into the measure description, mirroring
  `_compile_having_ratio`'s `CaveatRule` exactly. It is a tooltip, not a guardrail — CLAUDE.md's "a
  prompt instruction is not a guardrail" applies to a description field just as well.
- **Access control**, once the file is on a disk.
- **Slicer semantics**, per the divergence table above.

The row-cap warning (`MetricResult.truncated`) doesn't apply — Power BI loads every row — but that is
itself the point: the file holds the full data, not an aggregate.

## 6. Parity run (the Phase 9 evaluation, per §8's discipline)

Manual: Power BI Desktop is not scriptable here. Ground truth below is the engine's own output on the
real Kaggle Olist files (`poc2_analytics_engine/examples/real_olist_full_scale/data`), after a mapping
review rejecting everything POC 1 scored under 80% or mapped to `unknown`, with `TIME_ENTITY=orders`:

| Metric | Engine value |
|---|---|
| `revenue` | 16,008,872.12 |
| `orders` | 99,441 |
| `customers` | 99,441 |
| `aov` | 160.9886 |
| `repeat_purchase_rate` | 0.0 (caveated — see below) |

Procedure:

1. Unzip, open the `.pbip` in Desktop.
2. Set the **DataFolder** parameter to the unzipped `data` folder (trailing backslash), Close & Apply.
3. Drop each measure onto a card with no filters applied and compare against the table.

`repeat_purchase_rate` being 0.0 is correct, not a bug, and the export reproduces it faithfully: the
backend registry groups by `customer_id`, which on Olist is a per-order surrogate, so every group has
size 1. The engine attaches a caveat saying so; the DAX measure's description states the same
condition.

**Status: first open attempted; one defect found and fixed, parity comparison still outstanding.**

### Finding 1 — a relationship cannot carry a description

Desktop rejected the whole project with:

> There's a problem with the definition content in your Power BI Project.
> Property 'description' is unknown and is not expected in the situation it appears.

Cause: the serializer emitted a `/// Inactive: ...` line before each deactivated relationship,
explaining why it was deactivated. Tables, columns, measures, expressions and the model all have a
`description` property in the TMSL object model; **a Relationship does not**, so the description is
rejected wherever it appears there — and the failure is whole-project, not per-object.

Fixed by dropping that one description. The edge is still deactivated (`isActive: false`) and the
reason still reaches the reader through the export README, which is where they would look for it.
Regression test: `test_relationships_carry_no_description`. Every remaining `///` in the output was
audited to sit on an object that does have a `description`.

Worth remembering when adding anything to the generated model: TMDL will happily *write* a property
the object model doesn't accept, and the error surfaces only at open time, as a whole-project
rejection rather than a pointer to the offending line.

(A separate "There's a problem with WebView2" dialog appeared alongside it. That is a Desktop runtime
issue on the machine, not something in the generated project.)

## 7. Known limitations

- **No visuals.** The generated report is one empty page. Translating POC 3's `DashboardSpec` into
  PBIR visuals is real, separable work and the report layout format is the least stable part of PBIP.
- **The DataFolder step is manual.** Power Query has no way to find the folder a project was opened
  from, so it cannot be baked in.
- **One source file per table**, the same simplifying assumption `QueryExecutor.register_sources`
  makes.
- **CSV only**, following the rest of the platform.
- **No round trip.** Edits made in Desktop don't come back; a re-export overwrites.
