"""Real pipelines, real DuckDB, real sample data -- same "no mocks" convention every POC's own
integration tests already use. LLM-dependent assertions are gated on a real GROQ_API_KEY, same
convention as poc4_nl_chatbot/tests/test_real_olist_integration.py.
"""
import os

import pytest

from tests.conftest import SAMPLE_DATA_DIR

requires_groq = pytest.mark.skipif(not os.getenv("GROQ_API_KEY"), reason="requires a real GROQ_API_KEY")


def _upload_sample_files(client):
    files = [
        ("files", ("customers.csv", open(SAMPLE_DATA_DIR / "customers.csv", "rb"), "text/csv")),
        ("files", ("orders.csv", open(SAMPLE_DATA_DIR / "orders.csv", "rb"), "text/csv")),
        ("files", ("products.csv", open(SAMPLE_DATA_DIR / "products.csv", "rb"), "text/csv")),
    ]
    return client.post("/schema/discover", files=files)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_query_before_schema_discovery_returns_409(client):
    r = client.post("/analytics/query", json={"operation": "aggregate", "metric": "revenue"})
    assert r.status_code == 409


def test_dashboard_before_schema_discovery_returns_409(client):
    r = client.post("/dashboard/generate")
    assert r.status_code == 409


def test_chat_before_schema_discovery_returns_409(client):
    r = client.post("/chat/ask", json={"question": "What was total revenue?"})
    assert r.status_code == 409


def test_schema_discover_rejects_non_csv(client):
    files = [("files", ("orders.xlsx", b"not really excel", "application/vnd.ms-excel"))]
    r = client.post("/schema/discover", files=files)
    assert r.status_code == 400


@requires_groq
def test_full_flow_against_sample_data(client):
    r = _upload_sample_files(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entities"]

    query = {"operation": "aggregate", "metric": "revenue"}
    r = client.post("/analytics/query", json=query)
    assert r.status_code == 200, r.text
    assert r.json()["shape"] == "scalar"

    r = client.post("/dashboard/generate")
    assert r.status_code == 200, r.text
    assert r.json()["components"]

    r = client.post("/chat/ask", json={"question": "What was total revenue?"})
    assert r.status_code == 200, r.text
    assert "refused" in r.json()
