"""Dump the FastAPI app's OpenAPI schema to a file, without running a server or touching infra.

Phase 8's frontend generates every TypeScript type from this (via openapi-typescript) instead of
hand-maintaining mirrors of Pydantic models that live across insightflow_core, POC 3 and POC 4.
Those models have already changed under us once -- MetricResult gained its `shape` field in POC 4
M3, which touched all three POCs -- so a hand-written TS copy would drift silently.

Usage:  uv run python scripts/dump_openapi.py ../frontend/src/api/openapi.json
"""
import json
import os
import sys
from pathlib import Path

# create_app() refuses to start on an empty JWT_SECRET. Generating a schema signs no tokens and
# serves no requests, so a placeholder is sufficient -- but only when the environment hasn't
# supplied a real one, so this can never mask a genuinely misconfigured .env.
os.environ.setdefault("JWT_SECRET", "openapi-schema-dump-placeholder")

from insightflow_backend.main import create_app  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <output.json>")

    # Neither build_storage() nor build_redis_client() opens a connection eagerly (boto3 and
    # redis-py both connect lazily), so this runs with no Postgres/MinIO/Redis available.
    schema = create_app().openapi()

    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys so regenerating produces a minimal, reviewable diff when a model changes.
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
