%% POC 1 - Schema Discovery + Relationship Detection (revised at closure)
%% InsightFlow Project
%%
%% Revision note: this replaces the original planning-stage diagram. Changes from the
%% original plan, found while building and stress-testing the pipeline across 8 eval
%% datasets:
%%   - Candidate generation is TWO independent paths merged together, not one step.
%%     Path 1 (name similarity) alone missed real relationships between cryptically-named
%%     columns (eval_hard, eval_real_olist's "buyer_ref"/"cust_ref"-style real IDs). Path 2
%%     (shared semantic_type from the mapper) was added specifically to catch those.
%%   - The LLM's job in relationship detection was narrowed. It originally also judged
%%     *direction*; that was found to be unreliable and was never actually wired into the
%%     final Relationship object anyway. Direction is now decided deterministically from
%%     column uniqueness (FK = lower uniqueness -> PK = higher uniqueness), not the LLM.
%%   - Structured LLM output uses JSON mode with the real pydantic JSON Schema in the
%%     prompt, not LangChain's default function-calling method — several Groq-hosted
%%     models answered in prose instead of calling the forced tool under function-calling.
%%   - The evaluation harness grew a second script, eval_all.py, that runs every eval_*
%%     dataset through steps 1-6 and scores each one, not just a single manual run.
%%   - "Confidence calibration" (step 7) was never implemented — evaluation is
%%     precision/recall only. Removed from this diagram rather than left as an
%%     aspirational label.
```mermaid
flowchart TD
    A["Input Files\n(orders.csv, customers.csv, products.csv, ...)"] --> B["1. File Parser\n(Pandas)"]
    B --> C["2. Data Profiler\ncol name, dtype, null%, cardinality,\nmin/max, sample values\n-- deterministic --"]
    C --> D["3. Column Type Inference\n(identifier, date, numeric, categorical, text)\n-- deterministic rules --"]
    D --> E["4. Semantic Field Inference\n(LLM, JSON mode + real JSON Schema in prompt)\nmaps columns -> canonical fields\nwith confidence scores"]
    E --> E1{"Confidence\n>= threshold?"}
    E1 -- "No" --> E2["Flag: needs_confirmation\n(review queue -- HITL hook, not yet built)"]
    E1 -- "Yes" --> F["5. Relationship Detection"]
    E2 --> F

    subgraph F["5. Relationship Detection"]
        direction TB
        F1a["Path 1: Name-similarity\ncandidates (cross-file column\nname match >= threshold)"]
        F1b["Path 2: Shared semantic_type\ncandidates (from step 4's mapper output,\nregardless of naming)"]
        F1a --> F1m["Merge + dedupe candidates"]
        F1b --> F1m
        F1m --> F2["LLM Reasoning\nis_valid_relationship? + confidence\n(direction NOT asked -- see note)"]
        F2 --> F3["Deterministic Validation\n- re-check value overlap\n- direction from column uniqueness\n  (lower uniqueness = FK = 'from',\n   higher uniqueness = PK = 'to')"]
    end

    F --> G["6. Canonical Semantic Model\nEntities + SemanticFields + Relationships\n(serialized as JSON, pydantic)"]
    G --> H["7. Evaluation\nprecision/recall vs. hand-labeled ground truth\n(single dataset: run_poc1.py,\nall eval_* datasets: eval_all.py)"]

    style A fill:#e8f0fe,stroke:#4285f4
    style G fill:#e6f4ea,stroke:#34a853
    style H fill:#fef7e0,stroke:#f9ab00
    style E fill:#fce8e6,stroke:#ea4335
    style F fill:#fce8e6,stroke:#ea4335
```

## Known gaps this diagram doesn't show (carried forward, not fixed)

- **Hub-and-spoke false positives**: if the same key appears in 3+ files (e.g. `order_id` in
  `orders`, `order_items`, and `payments`), step 5 can validate a direct relationship between
  two files that are only actually related *transitively*, through a third. Found via
  `eval_real_olist`; not yet fixed.
- **Direction on a genuine uniqueness tie** (both sides equally unique) falls back to whatever
  order files were passed to the pipeline in — not deterministic across different call orders.
  Found via `eval_real_olist`; not yet fixed.
- **No same-file (self-referential) relationship detection** — step 5's candidate generation
  only ever compares columns across *different* files. This is a scope decision, documented via
  `eval_selfref`, not a bug being chased.
