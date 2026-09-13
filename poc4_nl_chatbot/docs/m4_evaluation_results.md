# M4 — Real-Groq Evaluation Results

Run via `scripts/run_m4_evaluation.py` against `data/refusal_benchmark.json`'s 16 fixtures, using
POC 4's own sample dataset (`data/semantic_model.json` + `data/raw/sample`,
`registry.bootstrap_registry()`) and `GroqLLMClient` (model: `openai/gpt-oss-120b`, from the same
`.env` POC 3 already uses -- copied, not referenced across POC folders, per the top-level
README's "self-contained POC" rule).

## Final result (after the prompt fix below)

```
16 questions -- intent 8/16, plan 8/16, numeric 4/16, refusal 16/16, hallucinations 0
```

- **Refusal accuracy: 16/16** — every one of the 8 answerable and 8 unanswerable fixtures was
  classified correctly.
- **Intent/plan accuracy: 8/8** (of the answerable fixtures) — every answerable question resolved
  to the exact expected metric and operation.
- **Numeric accuracy: 4/4** (of the fixtures with a defined `expected_value` — the 4 plain
  `AGGREGATE` questions; `GROUP_BY`/`GROWTH_BY_DIMENSION` results aren't a single scalar, and
  `revenue_growth` is genuinely undefined for this dataset, so neither has a meaningful
  `expected_value` to check against).
- **Hallucinations: 0** — no question the ground truth says is unanswerable was ever answered
  with a number.

Real numbers computed by the deterministic engine, not the LLM, sanity-checked against the sample
dataset used throughout this POC's own tests: revenue=790.0, orders=7.0, aov≈112.857,
repeat_purchase_rate=0.5, revenue by category={Gadgets: 660.0, Home: 130.0}, revenue by
region={North: 380.0, South: 320.0, West: 90.0}.

## Two real findings from these runs

1. **`revenue_growth` for `LAST_QUARTER` is genuinely undefined against this dataset** — the
   comparison period has zero matching orders, so `NULLIF(0, 0)`'s division is legitimately
   `NULL`. The planner correctly chose `revenue_growth`/`GROWTH`/`LAST_QUARTER`, the engine
   correctly returned `MetricResult(shape="scalar", value=None)` (the exact fix from this
   session's `MetricResult.shape` change), and `InsightGenerator` correctly explained it as
   undefined rather than fabricating a number or crashing. A clean, honest answer to a question
   the data can't actually support with this specific relative-time window — not a bug.
2. **Real planner inconsistency, found and fixed via these runs**: on an early run, "What is our
   repeat purchase rate?" (a plain `AGGREGATE` question against a `HAVING_RATIO` metric, valid by
   every rule `QuestionValidator` enforces) was incorrectly refused —
   `reason: "repeat_purchase_rate metric is not available for the allowed operations"`. The
   prompt listed which metrics are restricted to `group_by`/`growth_by_dimension`/`growth`, but
   never explicitly said *any* metric is valid for plain `aggregate`; the model over-generalized
   the restriction. **Fixed** by adding one explicit sentence to `QuestionPlanner._build_prompt`
   ("These two restrictions apply ONLY to group_by/growth_by_dimension/growth... ANY metric...
   is valid for plain operation=aggregate"). Confirmed fixed: the same question was answered
   correctly (`value: 0.5`) on every subsequent run. Regression test:
   `tests/test_question_planner.py::test_planner_prompt_clarifies_aggregate_allows_any_metric`.

This is the same class of finding — and the same fix pattern (one clarifying sentence, a
regression test locking it in) — as POC 3's own real-Groq runs found repeatedly in
`DashboardPlanner`'s prompt (see `poc3_dashboard_generation/docs/class_diagram.md`'s "Found via a
second/third real Groq/real-Olist run" entries).

## Sample real answerable exchanges

> **Q: "Which category drove the change in revenue last quarter?"**
> Plan: `revenue` / `growth_by_dimension` / `dimension=category` / `time_expression=last_quarter`
> Result: `Gadgets: 0.0 -> 120.0 (Δ +120.00)`, `Home: 0.0 -> 80.0 (Δ +80.00)`
> Explanation: "Gadgets drove the revenue change, increasing from 0.0 to 120.0 (Δ +120.00), which
> is larger than Home's increase of 0.0 to 80.0 (Δ +80.00)."

> **Q: "How much did revenue grow last quarter?"**
> Plan: `revenue_growth` / `growth` / `time_expression=last_quarter`
> Result: `value=None`
> Explanation: "Revenue growth for the last quarter is undefined, so no growth figure can be
> provided."

## Sample real refusal (architecture doc §16's own example)

> **Q: "What is our website conversion rate?"**
> `answerable=false`
> Reason: "Conversion rate requires visitor/session data which is not available in the listed
> metrics/dimensions."

## Not yet covered by this benchmark

- Multi-turn conversation, RAG/few-shot planning, and anything else named out of scope in
  `hld.md`'s Non-goals section — these fixtures only exercise v1's single-turn scope.
- A larger, real (not hand-built sample) dataset the way `poc4_nl_chatbot/tests/
  test_real_olist_integration.py` already exercises the deterministic layer against POC 1's
  actual Olist output — that test runs hand-built `QuestionIntent`s (no planner call); running
  the *planner* itself against real Olist output (with its ambiguous `category`/`region` and
  `payments`-has-no-time-field constraints) through real Groq is a natural next real-data check,
  not yet done.
