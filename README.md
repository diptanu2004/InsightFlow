# InsightFlow

AI-powered, schema-independent e-commerce analytics platform. See the full architecture and
design decisions in `AI_Ecommerce_Analytics_Project_Architecture.md` (kept in the Claude project).

## Structure

Each POC is a **self-contained project** — its own `pyproject.toml`, its own `uv`-managed
virtual environment, its own Dockerfile — so they can be built, tested, and run independently
without one POC's dependencies or state leaking into another.

```
InsightFlow/
├── poc1_schema_discovery/     # Phase 1 — schema discovery + relationship detection
├── poc2_analytics_engine/     # Phase 2 — deterministic analytics engine (implemented)
├── poc3_dashboard_generation/ # Phase 3 — dashboard spec generation (implemented, first pass)
├── poc4_nl_chatbot/           # Phase 4 — natural language analytics chatbot (not yet started)
└── README.md                  # you are here
```

## Why separate folders per POC

Per the architecture doc's development order (§29), the project is built as a sequence of
isolated POCs before anything is integrated into the final FastAPI app — this mirrors that:

- Each POC folder can be `cd`-ed into and run on its own (`uv sync && uv run ...`).
- No shared virtual environment or dependency conflicts between POCs.
- When it's time to integrate, combining them is mostly a **folder-structure change**: each
  POC's `src/insightflow/<module>` maps onto a service boundary in the target FastAPI app
  (`app/services/schema`, `app/services/analytics`, `app/services/dashboard`, `app/services/ai`,
  per §19 of the architecture doc) — not a rewrite.
- Semantic models, metrics, and other artifacts produced by an earlier POC (e.g. POC 1's
  `SemanticModel` JSON) can be fed into the next POC's tests/fixtures without them sharing code.

## POCs

| POC | Folder | Status |
|---|---|---|
| 1 — Schema Discovery + Relationship Detection | `poc1_schema_discovery/` | Closed — see its own README's "POC 1 status: closed" |
| 2 — Analytics Engine | `poc2_analytics_engine/` | Closed — verified end-to-end against a sample dataset and the real, full-scale (~99.4k order) Olist dataset; see its own README's "POC 2 status: closed" |
| 3 — Dashboard Generation | `poc3_dashboard_generation/` | Implemented, first pass — not yet closed (no real Groq run of the planner in this environment, no multi-dataset evaluation benchmark yet); see its own README's "POC 3 status" |
| 4 — Natural Language Analytics Chatbot | `poc4_nl_chatbot/` | Closed — M1-M4 all done; 69 tests passing plus a real-Groq M4 evaluation run (16/16 refusal, 8/8 intent, 8/8 plan, 4/4 numeric accuracy, 0 hallucinations); found and fixed 3 real bugs in the shared `insightflow_core` engine and 1 real planner-prompt bug along the way; see its own `docs/hld.md`/`docs/class_diagram.md`/`docs/m4_evaluation_results.md` |

See each POC's own `README.md` for setup and run instructions.
