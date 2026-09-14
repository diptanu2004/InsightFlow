"""An exhausted AI-provider quota is a temporary external condition, not a server fault.

Found when Groq's daily token limit ran out mid-session: groq.RateLimitError escaped chat/ask (and
would have escaped dashboard/generate) as an unhandled 500.
"""
import groq
import httpx
from fastapi.testclient import TestClient

from insightflow_backend.main import create_app


def test_provider_rate_limit_is_a_503_with_the_providers_retry_hint():
    app = create_app()

    def exhausted():
        response = httpx.Response(
            429,
            request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
            headers={"retry-after": "161"},
        )
        raise groq.RateLimitError("tokens per day (TPD) limit reached", response=response, body=None)

    app.add_api_route("/__test__/exhausted", exhausted)

    r = TestClient(app, raise_server_exceptions=False).get("/__test__/exhausted")

    assert r.status_code == 503
    assert r.headers["retry-after"] == "161"
    assert "usage limit" in r.json()["detail"]


def test_provider_timeouts_and_outages_are_503s():
    app = create_app()
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")

    def timed_out():
        raise groq.APITimeoutError(request=request)

    def provider_down():
        raise groq.InternalServerError("upstream error", response=httpx.Response(502, request=request), body=None)

    app.add_api_route("/__test__/timeout", timed_out)
    app.add_api_route("/__test__/down", provider_down)
    client = TestClient(app, raise_server_exceptions=False)

    for path in ("/__test__/timeout", "/__test__/down"):
        r = client.get(path)
        assert r.status_code == 503, path
        assert "didn't respond" in r.json()["detail"]


def test_other_provider_error_responses_are_503s_and_rate_limits_keep_their_own_message():
    """A too-large explanation prompt came back from Groq as a 413 and escaped as a raw 500."""
    app = create_app()
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")

    def too_large():
        raise groq.APIStatusError("Request too large", response=httpx.Response(413, request=request), body=None)

    def rate_limited():
        raise groq.RateLimitError("TPD", response=httpx.Response(429, request=request), body=None)

    app.add_api_route("/__test__/too-large", too_large)
    app.add_api_route("/__test__/rate-limited", rate_limited)
    client = TestClient(app, raise_server_exceptions=False)

    too_large_response = client.get("/__test__/too-large")
    assert too_large_response.status_code == 503 and "couldn't process" in too_large_response.json()["detail"]
    # The more specific handler still wins for its subclass.
    assert "usage limit" in client.get("/__test__/rate-limited").json()["detail"]
