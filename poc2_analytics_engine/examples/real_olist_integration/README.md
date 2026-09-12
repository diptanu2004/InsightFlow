# Real-world integration test: real Olist data, real POC 1 output

Everything under `data/` is an **unmodified copy** of real files:
`data/raw/*.csv` is copied byte-for-byte from
`poc1_schema_discovery/data/raw/eval_real_olist/`, and `data/semantic_model.json` is copied
byte-for-byte from `poc1_schema_discovery/olist_model.json` — POC 1's actual, previously-produced
output for this dataset. Neither was edited to make this test pass more easily; see
`../../docs/real_world_integration_test.md` for the full write-up (dataset provenance, why a
fresh POC 1 run wasn't feasible in this environment, every registry judgment call, results, and
five real gaps this surfaced between POC 1's actual output shape and what POC 2 assumed).

## Run it

```
uv run python examples/real_olist_integration/run_real_test.py
```

No `--semantic-model`/`--data-dir` flags needed — the script points at its own `data/` directory.
