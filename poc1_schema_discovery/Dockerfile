# InsightFlow — POC 1 image.
# Today this runs the schema-discovery CLI. The Phase 5 FastAPI layer now lives in the
# top-level backend/ package (it imports this POC as insightflow-schema-discovery), not here.

FROM python:3.11-slim

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first for better layer caching
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev

# Copy source
COPY src ./src
COPY scripts ./scripts

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Default: run the POC 1 CLI help. Override CMD (docker run ... <args>) to
# actually process files, e.g.:
#   docker run --env-file .env insightflow \
#     python scripts/run_poc1.py data/raw/sample/orders.csv ...
CMD ["python", "scripts/run_poc1.py", "--help"]
