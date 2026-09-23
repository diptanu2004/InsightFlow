"""Uploads the engine can't read, and data that fails to parse at query time.

Found on a real Superstore export with its "People" and "Returns" sheets appended below the orders: pandas-based
schema discovery accepted it, then every dashboard and question was a raw 500 from inside DuckDB.
"""
import duckdb

from tests.infra import requires_infra, requires_postgres, run_pending_jobs


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _project(client, suffix):
    token = client.post("/auth/register", json={"email": f"unreadable-{suffix}@example.com", "password": "correct-horse-battery-staple"}).json()["access_token"]
    org = client.post("/organizations", json={"name": f"Unreadable {suffix}"}, headers=_auth(token)).json()
    project = client.post(f"/organizations/{org['id']}/projects", json={"name": "p"}, headers=_auth(token)).json()
    return token, project


@requires_infra
def test_a_csv_with_a_second_table_appended_fails_discovery_with_a_readable_reason(committing_client):
    import uuid

    token, project = _project(committing_client, uuid.uuid4().hex[:8])
    rows = "".join(f"O{i},West,{i}\n" for i in range(25_000))
    content = f"order_id,region,sales\n{rows}Person,Region\nAnna,West\n".encode()

    submitted = committing_client.post(
        f"/projects/{project['id']}/schema/discover", files=[("files", ("superstore.csv", content, "text/csv"))], headers=_auth(token)
    )
    assert submitted.status_code == 202, submitted.text
    run_pending_jobs()  # fails on the check, before any LLM call -- so no GROQ_API_KEY needed

    job = committing_client.get(f"/projects/{project['id']}/schema/jobs/{submitted.json()['id']}", headers=_auth(token)).json()
    assert job["status"] == "failed"
    assert job["error"].startswith('"superstore.csv" can\'t be read as a single table: line 25002 has 2 columns')
    assert committing_client.get(f"/projects/{project['id']}/datasets", headers=_auth(token)).json() == []


@requires_postgres
def test_data_that_fails_to_parse_at_query_time_is_a_422_not_a_500(client):
    message = (
        "Invalid Input Error: CSV Error on Line: 9996\nOriginal Line: \nPerson,Region\n"
        "Expected Number of Columns: 21 Found: 2\n  file = .dataset_cache\92ab\superstore.csv\n  sample_size = 20480"
    )

    def boom():
        raise duckdb.InvalidInputException(message)

    client.app.add_api_route("/__test_duckdb_error", boom)
    r = client.get("/__test_duckdb_error")

    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail.startswith("This dataset's data couldn't be read.")
    assert '"superstore.csv" can\'t be read as a single table: line 9996 has 2 columns where the header has 21' in detail
    assert "sample_size" not in detail
