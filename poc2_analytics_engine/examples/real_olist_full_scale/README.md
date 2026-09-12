# Real-world SCALE integration test: the full Olist dataset, real POC 1 mapping

Everything under `data/raw/` is an **unmodified copy** of the real, full **Brazilian E-Commerce
Public Dataset by Olist** (~99.4k orders), downloaded directly from Kaggle by the user — not the
~8-16 row referential sample used in `../real_olist_integration/`. `data/semantic_model.json` is
the same real POC 1 output used there (`poc1_schema_discovery/olist_model.json`), reused
unchanged because the two datasets share the exact same CSV column headers — see
`../../docs/real_world_integration_test.md`'s "Part 2" for why that's valid and for the full
write-up (results, and two real `SQLCompiler` bugs this scale test found and fixed that no amount
of testing against the tiny sample could have caught).

Only the 7 CSVs the registered metrics actually use are here — `geolocation` and `order_reviews`
from the original Kaggle download aren't needed by any of the ten target metrics and were left
out.

## Run it

```
uv run python examples/real_olist_full_scale/run_full_scale_test.py
```

No `--semantic-model`/`--data-dir` flags needed — the script points at its own `data/` directory.
Prints wall-clock and engine-reported timing per query alongside each result.
