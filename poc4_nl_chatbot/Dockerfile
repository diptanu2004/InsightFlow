# InsightFlow — POC 4 image.
# Today this runs the NL-chatbot CLI. The Phase 5 FastAPI layer now lives in the
# top-level backend/ package (it imports this POC as insightflow-chatbot), not here.
#
# BUILD CONTEXT: builds from the top-level InsightFlow/ directory, not from poc4_nl_chatbot/
# itself -- same reason as poc2/poc3's Dockerfiles (pyproject.toml depends on the sibling
# insightflow-core package as a local editable path dependency, so the build needs that sibling
# directory in its context). Build with:
#   docker build -f poc4_nl_chatbot/Dockerfile -t insightflow-poc4 .
# (run from the InsightFlow/ root).

FROM python:3.11-slim

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# The shared engine package lives as a sibling in the build context — see
# poc2_analytics_engine/Dockerfile's identical COPY for why this exact path.
COPY insightflow_core /insightflow_core

# Install dependencies first for better layer caching
COPY poc4_nl_chatbot/pyproject.toml poc4_nl_chatbot/uv.lock* ./
RUN uv sync --no-dev

# Copy source
COPY poc4_nl_chatbot/src ./src
COPY poc4_nl_chatbot/scripts ./scripts
# Small sample dataset the documented example command points at by relative path — allowlisted
# in the top-level .dockerignore the same way POC 2/3 established.
COPY poc4_nl_chatbot/data ./data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Requires GROQ_API_KEY at runtime (docker run -e GROQ_API_KEY=... ...) -- QuestionPlanner and
# InsightGenerator are real LLM calls.
CMD ["python", "scripts/run_poc4.py", "--help"]
