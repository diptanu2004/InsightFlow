# InsightFlow — POC 1: Schema Discovery + Relationship Detection

Given arbitrary CSV/Excel files with unfamiliar column names, this pipeline:
1. Profiles each column (deterministic stats — no LLM).
2. Infers a rough type per column (deterministic rules).
3. Maps columns to canonical semantic fields (LLM, structured output).
4. Detects cross-file relationships (statistical candidates → LLM reasoning → deterministic validation).
5. Assembles a canonical `SemanticModel` (JSON-serializable pydantic object).
6. Scores the result against hand-labeled ground truth.

This is a pure algorithm test — no web server, no UI, no human-in-the-loop yet. See
`Non-Goals` in the architecture doc: HITL, FastAPI, auth, and the frontend are later phases.

## Project layout

```
src/insightflow/
├── config.py              # env-based settings (.env)
├── models/                 # pydantic domain models (shared everywhere, incl. future API)
├── parsing/                # FileParser — CSV/Excel -> DataFrame
├── profiling/               # DataProfiler — deterministic column stats
├── inference/               # TypeInferer — rule-based column type classifier
├── llm/                     # LLMClient interface + ChatGroq implementation
├── semantic/                 # SemanticVocabulary + SemanticMapper (LLM step)
├── relationships/            # RelationshipDetector (candidates -> LLM -> validation)
├── evaluation/                # GroundTruth + Evaluator (precision/recall)
└── pipeline.py                # SchemaDiscoveryPipeline — single orchestration entry point

scripts/run_poc1.py            # CLI entry point
data/raw/sample/                # sample orders/customers/products CSVs
data/ground_truth/               # hand-labeled expected mappings/relationships
tests/                           # unit tests for the deterministic (non-LLM) layers
```

## Setup (using `uv`)

1. Install `uv` if you don't have it: https://docs.astral.sh/uv/getting-started/installation/
2. From inside `InsightFlow/poc1_schema_discovery/`:
   ```bash
   uv sync
   ```
   This creates `.venv/` and installs everything from `pyproject.toml`.
3. Copy `.env.example` to `.env` and add your Groq API key (get one at https://console.groq.com):
   ```bash
   cp .env.example .env
   ```

## Run POC 1 on the sample dataset

```bash
uv run python scripts/run_poc1.py \
    data/raw/sample/orders.csv \
    data/raw/sample/customers.csv \
    data/raw/sample/products.csv \
    --ground-truth data/ground_truth/sample_ground_truth.json
```

This prints the inferred `SemanticModel`, writes it to `semantic_model.json`, and prints a
precision/recall report comparing the output to the ground truth file.

## Run the full evaluation suite

Once you have multiple eval datasets (e.g. `data/raw/eval_easy/`, `data/raw/eval_medium/`, ...
each with a matching `data/ground_truth/eval_<name>_ground_truth.json`):

```bash
uv run python scripts/eval_all.py
```

This runs every `eval_*` dataset through the pipeline, scores each against its ground truth,
prints a combined precision/recall table, and writes the full results to `eval_results.json`.
A dataset is skipped (with a warning) if it has no matching ground truth file; a dataset that
errors out doesn't stop the rest of the run.

## Run tests

Only the deterministic layers (profiler, type inferer) are tested — no API key required:

```bash
uv run pytest
```

## Design notes (for future you)

- **Pluggable into FastAPI with minimal changes.** `SchemaDiscoveryPipeline.run(filepaths) -> SemanticModel`
  is the only thing a route needs to call. All models are pydantic, so FastAPI can return
  a `SemanticModel` directly as a JSON response with zero extra serialization code.
- **LLM provider is swappable.** `SemanticMapper` and `RelationshipDetector` depend on the
  `LLMClient` interface (`llm/client.py`), not on `GroqLLMClient` directly. Swapping providers
  means writing one new class, not touching pipeline logic.
- **HITL hook already exists.** `SemanticMapping.needs_confirmation` and `RelationshipCandidate`
  (pre-validation) are the seams where a future confirmation UI plugs in — POC 1 doesn't need
  to know about it yet.
- **Docker-ready.** The `Dockerfile` installs via `uv sync` and currently runs the CLI; swapping
  the final `CMD` to `uvicorn insightflow.api:app ...` is the only change needed once the API
  layer is added — no rebuild of the dependency layer required.

## Known limitations (documented, not accidental)

`eval_selfref` is expected to score 0.0/0.0 on relationships, **and its ground truth
deliberately keeps a debatable mapping label rather than being tuned to pass**: it labels
`referred_by_customer_id` as `customer_id`, but the pipeline consistently tags it `unknown`
(low confidence, ~0.2) instead. Both are defensible — reusing `customer_id` for a different
role (the referrer, not the record's own customer) is itself a questionable ground-truth
choice, and `unknown` at low confidence is arguably the more honest answer, since it correctly
flags the column as needing a human decision rather than confidently mislabeling it. The
ground truth is intentionally left as-is (not adjusted to match the pipeline's answer) so this
dataset's ~0.67 mapping score and 0.0 relationship score are both expected, not regressions.

The relationship failure specifically is structural, not a scoring quirk:
`RelationshipDetector` only ever compares columns *across different files*
(both the name-similarity path and the semantic-type path skip `file_a == file_b` pairs).
A self-referential FK — e.g. `customers.referred_by_customer_id -> customers.customer_id`,
within the same file — is structurally out of scope for the current detector, not a bug it's
failing to catch. `eval_selfref/` exists to demonstrate this concretely rather than leave it
as a hypothesis. Extending the detector to same-file FKs would need its own design pass
(e.g. treating a column whose values are a subset of another column *in the same file* as a
candidate) — not attempted here to avoid quietly widening scope mid-POC.

Other gaps not yet exercised by any eval dataset:
- **Composite/multi-column keys** — every FK tested so far is a single column.
- **`top_k_for_llm` cap (default 10)** — no dataset yet has enough candidate columns to test
  what happens when a real relationship ranks below the top 10 candidates and never reaches
  the LLM reasoning step at all.
- **Scale** — all eval datasets are 4-12 rows per file. Value-overlap set computation and
  profiling behave differently on real-sized exports (thousands of rows).
- **All eval data is LLM-generated from one template/prompt.** Every dataset (including the
  adversarial and junction ones) shares a similar retail order/customer/product shape because
  one generator produced all of them. A dataset from a genuinely different source (a real
  public dataset, or a different domain entirely) is the next real test of generalization.

`eval_real_olist` is a small, referentially-correct subset of the real, public Brazilian
e-commerce dataset by Olist (7 files: orders, customers, order_items, products, sellers,
payments, category_translation — real hashed IDs, real Portuguese category codes, real nulls
from undelivered orders, and a genuine typo baked into the source data,
`product_name_lenght`). It surfaced findings the synthetic datasets never could, because none
of them had a genuinely 1:1 relationship, a key shared across three files, or more than one
plausible column per canonical field:

- **Direction on a genuine uniqueness tie depends on file argument order, not just alphabetical
  order.** Predicted in advance: `customer_id` in the real Olist schema is a per-order surrogate
  (that's why `customer_unique_id` exists separately), so `orders.customer_id` and
  `customers.customer_id` are both 100% unique in this sample — a true 1:1 tie. Confirmed:
  `payments.order_id -> orders.order_id` came back reversed as predicted. But the mechanism is
  worse than "no signal on ties" as originally written here — `generate_candidates` uses
  `list(dataframes.keys())`, which preserves whatever order the caller passed files in. On a
  genuine tie, the **same files in a different argument order can produce a different
  direction** for the same relationship. That's a reproducibility gap, not just an edge case.
- **A hub-and-spoke false positive — new, not predicted.** `order_id` appears in three files
  (`orders`, `order_items`, `payments`). The detector correctly found `order_items -> orders`
  and `orders -> payments`, but also reported a *direct* relationship between `order_items` and
  `payments` — which don't actually reference each other; they're only related transitively,
  through `orders`. No synthetic dataset ever had a key shared across three files, so this
  never had a chance to surface. Real fix needed: detect when two "relationships" share an
  identical value-set on both ends and treat them as transitively connected rather than
  independently validating each pair.
- **Vocabulary is too coarse once real data offers more than one plausible column per field —
  confirmed, with a miscalibration on top.** Real customer geography (`zip_code_prefix`,
  `city`, `state`) all collapsed onto the single `region` field, as did seller geography
  (`seller_city`, `seller_state` — anticipated in advance). But confidence didn't track risk
  consistently: `seller_state`/`seller_city` landed at appropriately low confidence (0.6–0.7,
  correctly flagged `needs_confirmation`), while `customer_zip_code_prefix`/`customer_city` —
  arguably a *worse* fit for "region" than a state code is — landed at high confidence
  (0.85–0.9) and would NOT be flagged for review. The same pattern hit dates: `orders` has five
  real date columns, and `order_approved_at` got confidently (0.85) mapped to `transaction_date`
  even though `order_purchase_timestamp` already filled that slot in the same file — the mapper
  has no "already used this slot" awareness within a file.
- **Some disagreements were arguably my ground truth's fault, not the pipeline's** —
  `order_items.price -> price` (matches the vocabulary's literal "unit price" wording better
  than my chosen `revenue` label) and `payments.payment_value -> revenue` (a defensible
  alternate revenue source to the one I picked). Two real monetary columns competing for one
  "revenue" slot is itself a legitimate finding — the vocabulary doesn't yet distinguish
  line-item revenue from order-level payment total.

## Roadmap

This is Phase 1 of the development order in the architecture doc. It's deliberately self-contained
(own `pyproject.toml`, own venv, own Docker image) so POC 2, 3, 4 can each live in their own sibling
folder (`poc2_analytics_engine/`, etc.) under `InsightFlow/` without touching this one. When the app
is finally integrated, combining POCs is mostly a folder move: each POC's `src/insightflow/<module>`
maps onto a service boundary in the target FastAPI app (see `app/services/` in the architecture doc).

Diagrams for this POC live in `docs/pipeline_diagram.md` and `docs/class_diagram.md` — both were
revised at closure (see below) to match the implementation as it actually stands, not just the
original plan.

## POC 1 status: closed

Closed after 8 eval datasets (4 synthetic difficulty tiers, an adversarial false-positive test, a
many-to-many junction test, a documented-limitation self-referential test, and one real public
dataset) and several rounds of diagnosed-and-fixed bugs (JSON-mode field naming, relationship
direction, name-similarity-only candidate generation). The algorithm works well enough on
clean-to-moderately-messy multi-file data with single-column keys to justify building POC 2 on top
of the `SemanticModel` it produces — with the limitations above carried forward explicitly rather
than quietly left implicit.

**Carried forward as backlog, not blocking POC 2:**
- Hub-and-spoke false-positive relationships when a key is shared across 3+ files (found via
  `eval_real_olist`, not yet fixed)
- Direction on genuine uniqueness ties depends on file argument order (found via `eval_real_olist`,
  not yet fixed)
- No same-file (self-referential) FK detection (documented via `eval_selfref`, scope decision not
  a bug)
- No composite/multi-column key support
- Untested at real-world row counts (thousands+ rows) — all eval data is 4-16 rows per file
- Vocabulary is too coarse for real data with multiple plausible columns per canonical field
  (multiple dates, multiple geography granularities, multiple monetary columns) — found via
  `eval_real_olist`
- `top_k_for_llm` cap (default 10) still untested against a dataset with enough candidates to
  actually hit it

None of these were fixed reactively mid-POC on the theory that patching each one as found risks
tuning the code to the specific eval data rather than fixing the underlying design — e.g. the
hub-and-spoke false positive likely needs a transitivity check in `RelationshipDetector`, not a
threshold tweak, and that's a deliberate design decision for whoever picks it up next, not a quick
patch.
