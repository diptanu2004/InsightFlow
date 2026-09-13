# InsightFlow

AI-powered, schema-independent e-commerce analytics platform. See the full architecture and
design decisions in `AI_Ecommerce_Analytics_Project_Architecture.md` (kept in the Claude project).

## Structure

Each POC is a **self-contained project** — its own `pyproject.toml`, its own `uv`-managed
virtual environment, its own Dockerfile — so they can be built, tested, and run independently
without one POC's dependencies or state leaking into another.

```
InsightFlow/
├── poc1_schema_discovery/     # Phase 1 — schema discovery + relationship detection (closed)
├── insightflow_core/          # Shared engine (models, compilation, execution, validation, registry)
├── poc2_analytics_engine/     # Phase 2 — deterministic analytics engine (closed)
├── poc3_dashboard_generation/ # Phase 3 — dashboard spec generation (implemented, first pass)
├── poc4_nl_chatbot/           # Phase 4 — natural language analytics chatbot (closed)
├── backend/                   # Phase 5 — one FastAPI app wiring POC 1/3/4 + insightflow_core
│                               #   behind HTTP routes (implemented, not yet closed)
└── README.md                  # you are here
```

## Why separate folders per POC

Per the architecture doc's development order (§29), the project is built as a sequence of
isolated POCs before anything is integrated into the final FastAPI app — this mirrors that:

- Each POC folder can be `cd`-ed into and run on its own (`uv sync && uv run ...`).
- No shared virtual environment or dependency conflicts between POCs.
- Semantic models, metrics, and other artifacts produced by an earlier POC (e.g. POC 1's
  `SemanticModel` JSON) can be fed into the next POC's tests/fixtures without them sharing code.

Phase 5 (`backend/`) is that eventual integration: each POC's `src/insightflow/<module>` was
renamed to a unique package (`insightflow_schema_discovery`, `insightflow_dashboard`,
`insightflow_chatbot` — POC 2's engine already lives in `insightflow_core`) so the backend can
import all of them into one FastAPI process, mapping onto the service boundaries §19 of the
architecture doc describes (`app/services/schema`, `app/services/analytics`,
`app/services/dashboard`, `app/services/ai`) — see `backend/docs/hld.md`.

## POCs

| POC | Folder | Status |
|---|---|---|
| 1 — Schema Discovery + Relationship Detection | `poc1_schema_discovery/` | Closed — see its own README's "POC 1 status: closed" |
| 2 — Analytics Engine | `poc2_analytics_engine/` | Closed — verified end-to-end against a sample dataset and the real, full-scale (~99.4k order) Olist dataset; see its own README's "POC 2 status: closed" |
| 3 — Dashboard Generation | `poc3_dashboard_generation/` | Implemented, first pass — not yet closed (no real Groq run of the planner in this environment, no multi-dataset evaluation benchmark yet); see its own README's "POC 3 status" |
| 4 — Natural Language Analytics Chatbot | `poc4_nl_chatbot/` | Closed — M1-M4 all done; 69 tests passing plus a real-Groq M4 evaluation run (16/16 refusal, 8/8 intent, 8/8 plan, 4/4 numeric accuracy, 0 hallucinations); found and fixed 3 real bugs in the shared `insightflow_core` engine and 1 real planner-prompt bug along the way; see its own `docs/hld.md`/`docs/class_diagram.md`/`docs/m4_evaluation_results.md` |
| 5 — FastAPI Integration | `backend/` | Implemented, not yet closed — POC 1/2/3/4 packages renamed to resolve a top-level import collision, all 4 POCs' + `insightflow_core`'s test suites re-verified green standalone, and a full real-data/real-Groq end-to-end run (`/schema/discover` → `/analytics/query` → `/dashboard/generate` → `/chat/ask`) passes; the Docker build (`backend/Dockerfile`) is written but not yet verified (no Docker available in the environment it was built in); see its own `docs/hld.md`/`docs/class_diagram.md` |

See each POC's own `README.md` for setup and run instructions.
