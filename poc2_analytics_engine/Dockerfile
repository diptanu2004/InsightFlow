# InsightFlow — POC 2 image.
# Today this runs the analytics-engine CLI. The Phase 5 FastAPI layer now lives in the
# top-level backend/ package (it imports this POC as insightflow-analytics), not here.
#
# BUILD CONTEXT CHANGED: this now builds from the top-level InsightFlow/ directory, not from
# poc2_analytics_engine/ itself -- since pyproject.toml depends on the sibling insightflow-core
# package (a local editable path dependency, `../insightflow_core`, added when that engine code
# was extracted out of this POC — see insightflow_core/README.md), the build needs that sibling
# directory inside its context to install it. Build with:
#   docker build -f poc2_analytics_engine/Dockerfile -t insightflow-poc2 .
# (run from the InsightFlow/ root, not from inside poc2_analytics_engine/ — the old
# `docker build .` from inside this folder no longer has access to ../insightflow_core).

FROM python:3.11-slim

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# The shared engine package lives as a sibling in the build context and gets installed editable
# via pyproject.toml's [tool.uv.sources] path entry -- copied to the same relative path
# (/insightflow_core, sibling of /app) so that relative "../insightflow_core" resolves inside the
# image exactly like it does on disk.
COPY insightflow_core /insightflow_core

# Install dependencies first for better layer caching
COPY poc2_analytics_engine/pyproject.toml poc2_analytics_engine/uv.lock* ./
RUN uv sync --no-dev

# Copy source
COPY poc2_analytics_engine/src ./src
COPY poc2_analytics_engine/scripts ./scripts
# Sample data + fixtures the documented example commands (README.md, this file's own CMD
# comment) point at by relative path (data/queries/revenue.json, data/raw/sample, ...) — found
# missing during the POC 2 closure sweep: .dockerignore excludes `data/` (correctly, for the
# multi-MB real Olist examples under examples/), but nothing then re-added just the small sample
# dataset this POC actually ships example queries/fixtures against, so the documented `docker
# run` commands would fail inside the built image with no such file. This re-adds only that
# sample dataset, not `examples/` (deliberately still excluded — those are real Olist CSVs meant
# for local testing, not for shipping inside the image).
COPY poc2_analytics_engine/data ./data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Default: run the POC 2 CLI help. Override CMD (docker run ... <args>) to
# actually run a query, e.g.:
#   docker run insightflow-poc2 \
#     python scripts/run_poc2.py --query data/queries/revenue.json \
#       --semantic-model data/semantic_model.json --data-dir data/raw/sample
CMD ["python", "scripts/run_poc2.py", "--help"]
