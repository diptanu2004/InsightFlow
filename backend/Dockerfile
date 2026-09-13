# InsightFlow — Phase 5 backend image. Wires POC 1/3/4 + insightflow_core behind one FastAPI app.
#
# BUILD CONTEXT MUST BE THE REPO ROOT (not backend/): path dependencies in pyproject.toml
# (../insightflow_core, ../poc1_schema_discovery, etc.) need those sibling directories present
# at the same relative layout uv.sources declares. Build with:
#   docker build -f backend/Dockerfile -t insightflow-backend .

FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY insightflow_core ./insightflow_core
COPY poc1_schema_discovery ./poc1_schema_discovery
COPY poc3_dashboard_generation ./poc3_dashboard_generation
COPY poc4_nl_chatbot ./poc4_nl_chatbot
COPY backend ./backend

WORKDIR /app/backend
RUN uv sync --no-dev

ENV PATH="/app/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "insightflow_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
