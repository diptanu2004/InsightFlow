# InsightFlow — POC 3 image.
# Today this runs the dashboard-generation CLI. The Phase 5 FastAPI layer now lives in the
# top-level backend/ package (it imports this POC as insightflow-dashboard), not here.
#
# BUILD CONTEXT CHANGED: this now builds from the top-level InsightFlow/ directory, not from
# poc3_dashboard_generation/ itself -- same reason as poc2_analytics_engine/Dockerfile's own note
# (pyproject.toml depends on the sibling insightflow-core package as a local editable path
# dependency, so the build needs that sibling directory in its context). Build with:
#   docker build -f poc3_dashboard_generation/Dockerfile -t insightflow-poc3 .
# (run from the InsightFlow/ root).

FROM python:3.11-slim

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# The shared engine package lives as a sibling in the build context — see
# poc2_analytics_engine/Dockerfile's identical COPY for why this exact path.
COPY insightflow_core /insightflow_core

# Install dependencies first for better layer caching
COPY poc3_dashboard_generation/pyproject.toml poc3_dashboard_generation/uv.lock* ./
RUN uv sync --no-dev

# Copy source
COPY poc3_dashboard_generation/src ./src
COPY poc3_dashboard_generation/scripts ./scripts
# Small sample dataset the documented example command (README.md, this file's own CMD comment)
# points at by relative path -- allowlisted in the top-level .dockerignore the same way POC 2's
# closure sweep established (a parent .dockerignore exclusion can't be selectively un-excluded,
# so both the ignore file and this COPY have to agree).
COPY poc3_dashboard_generation/data ./data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Requires GROQ_API_KEY at runtime (docker run -e GROQ_API_KEY=... ...) -- DashboardPlanner is a
# real LLM call, unlike POC 2's image which needed no credentials at all.
CMD ["python", "scripts/run_poc3.py", "--help"]
