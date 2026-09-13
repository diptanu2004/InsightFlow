# Phase 5 Backend — Structure

```
backend/src/insightflow_backend/
├── main.py            create_app() -> FastAPI, attaches app.state.session, includes 5 routers
├── config.py           Settings -- union of every POC's own config.py + upload_root
├── session.py           SessionState (semantic_model, data_dir, cached pipelines)
│                        get_session(request), require_schema(session) -> 409
├── wiring.py            run_schema_discovery(), build_analytics_engine(), build_dashboard(),
│                        build_chatbot() -- each a thin call into one POC's own builder/pipeline
├── registry/
│   └── bootstrap.py     bootstrap_registry() -- canonical copy, see hld.md
└── routers/
    ├── health.py         GET  /health
    ├── schema.py          POST /schema/discover   (multipart upload, .csv only)
    ├── analytics.py       POST /analytics/query
    ├── dashboard.py       POST /dashboard/generate
    └── chat.py            POST /chat/ask
```

## Call graph per endpoint

**POST /schema/discover**
`session.reset()` → save uploads under `session.new_upload_dir()` →
`wiring.run_schema_discovery(paths)` (POC 1's `SchemaDiscoveryPipeline.run()`, then bridges
POC 1's `SemanticModel` into `insightflow_core`'s via the same JSON round-trip every POC script
already uses) → stored on `session.semantic_model` / `session.data_dir`.

**POST /analytics/query**
`require_schema()` → `session.get_or_build_engine()` (lazy: `wiring.build_analytics_engine()` →
`insightflow_core.pipeline.build_pipeline()`) → `engine.run(query)`.

**POST /dashboard/generate**
`require_schema()` → `session.get_or_build_dashboard_pipeline()` (lazy:
`wiring.build_dashboard()` → `insightflow_dashboard`'s `build_dashboard_pipeline()`, which builds
its own internal engine) → `.run()`.

**POST /chat/ask**
`require_schema()` → `session.get_or_build_chat_pipeline()` (lazy: reuses
`session.get_or_build_engine()`, then `wiring.build_chatbot()` →
`insightflow_chatbot`'s `build_question_answering_pipeline(..., engine=...)`) → `.answer(question)`.

## Models

No new response types beyond `AskRequest` (`{question: str}`, chat's request body — `Answer` has
no bare string input type to reuse). Every response model is imported directly from its owning
POC/`insightflow_core`: `SemanticModel`, `AnalyticalQuery`/`MetricResult`, `HydratedDashboard`,
`Answer`.
