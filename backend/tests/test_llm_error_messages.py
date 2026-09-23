"""A provider failure must read the same whether it happened in a request or in the worker.

frontend/docs/hld.md finding 28: main.py's handlers are FastAPI exception handlers, so schema
discovery -- which runs in RQ, outside the ASGI app -- stored str(exception) on the Job row and the
upload panel rendered Groq's raw 429 body, organization id and billing link included.
"""
import groq
import httpx
import pytest

from insightflow_backend.jobs import _describe
from insightflow_backend.llm_errors import is_transient, provider_message

QUOTA_BODY = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` in "
    "organization `org_01m01wp9peeyh9jzteppjfvt58` service tier `on_demand` on tokens per day (TPD): "
    "Limit 200000, Used 200000. Upgrade to Dev Tier today at https://console.groq.com/settings/billing'}}"
)


def _response(status: int) -> httpx.Response:
    return httpx.Response(status_code=status, request=httpx.Request("POST", "https://api.groq.com/v1/chat"))


def _rate_limited() -> groq.RateLimitError:
    return groq.RateLimitError(QUOTA_BODY, response=_response(429), body=None)


def test_a_quota_failure_never_reaches_a_job_row_verbatim():
    stored = _describe(_rate_limited())
    assert "org_01m" not in stored
    assert "console.groq.com" not in stored
    assert "usage limit has been reached" in stored
    # Transient, so the panel should invite another go rather than look permanent.
    assert "Re-upload" in stored


def test_the_worker_and_the_http_handlers_say_the_same_thing():
    error = _rate_limited()
    assert provider_message(error) is not None
    assert _describe(error).startswith(provider_message(error))


def test_a_rejected_request_is_not_advertised_as_retryable():
    too_large = groq.APIStatusError("payload too large", response=_response(413), body=None)
    assert not is_transient(too_large)
    assert "Re-upload" not in _describe(too_large)


def test_a_non_provider_failure_is_still_reported_as_itself():
    # A malformed CSV's own message is the useful part; don't dress it up as an outage.
    assert provider_message(ValueError("line 9996 has 2 columns")) is None
    assert _describe(ValueError("line 9996 has 2 columns")) == "line 9996 has 2 columns"
