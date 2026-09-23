"""RetryingLLMClient -- frontend/docs/hld.md finding 27.

The survey's third run is the motivation: six of ten journeys derailed by transient provider
failures, four losing schema discovery outright, and an immediate unchanged re-run passing three of
them. The two behaviours worth protecting are that a blip is retried and that a *daily* quota is
not waited out -- Groq answers both with a 429, and only one of them can succeed inside a request.
"""
import groq
import httpx
import pytest
from pydantic import BaseModel

from insightflow_backend.llm_retry import RetryingLLMClient


class Answer(BaseModel):
    value: str


def _response(status: int, retry_after: str | None = None) -> httpx.Response:
    headers = {"retry-after": retry_after} if retry_after else {}
    return httpx.Response(
        status_code=status, headers=headers, request=httpx.Request("POST", "https://api.groq.com/v1/chat")
    )


def _rate_limited(retry_after: str | None = None) -> groq.RateLimitError:
    return groq.RateLimitError("rate limited", response=_response(429, retry_after), body=None)


def _connection_error() -> groq.APIConnectionError:
    return groq.APIConnectionError(request=httpx.Request("POST", "https://api.groq.com/v1/chat"))


class Flaky:
    """Fails with `failures` copies of `error`, then succeeds."""

    def __init__(self, error: Exception, failures: int):
        self._error, self._remaining, self.calls = error, failures, 0

    def generate_structured(self, prompt: str, output_schema):
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise self._error
        return output_schema(value="ok")


class Recorder:
    def __init__(self):
        self.slept: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)


def _client(inner, **kwargs) -> RetryingLLMClient:
    kwargs.setdefault("jitter", lambda: 1.0)
    return RetryingLLMClient(inner, **kwargs)


def test_a_dropped_connection_is_retried_and_the_call_succeeds():
    inner, slept = Flaky(_connection_error(), failures=1), Recorder()
    result = _client(inner, sleep=slept).generate_structured("prompt", Answer)
    assert result.value == "ok"
    assert inner.calls == 2
    assert len(slept.slept) == 1


def test_a_burst_rate_limit_with_a_short_hint_waits_exactly_that_long():
    inner, slept = Flaky(_rate_limited(retry_after="2"), failures=1), Recorder()
    assert _client(inner, sleep=slept).generate_structured("prompt", Answer).value == "ok"
    assert slept.slept == [2.0]


def test_a_daily_quota_is_not_waited_out():
    """Groq's per-day ceiling answers with a retry-after in minutes; blocking on it helps nobody.

    Three separate keys hit this during the survey. The caller gets the same clean sentence it got
    before this wrapper existed, immediately.
    """
    inner, slept = Flaky(_rate_limited(retry_after="826"), failures=1), Recorder()
    with pytest.raises(groq.RateLimitError):
        _client(inner, sleep=slept).generate_structured("prompt", Answer)
    assert inner.calls == 1, "a quota wall must not be retried"
    assert slept.slept == []


def test_a_request_the_provider_rejected_on_its_merits_is_not_retried():
    # A 413 for an over-long prompt is rejected identically every time.
    too_large = groq.APIStatusError("payload too large", response=_response(413), body=None)
    inner, slept = Flaky(too_large, failures=1), Recorder()
    with pytest.raises(groq.APIStatusError):
        _client(inner, sleep=slept).generate_structured("prompt", Answer)
    assert inner.calls == 1
    assert slept.slept == []


def test_it_gives_up_after_the_attempt_budget_and_raises_the_real_error():
    inner, slept = Flaky(_connection_error(), failures=99), Recorder()
    with pytest.raises(groq.APIConnectionError):
        _client(inner, sleep=slept, attempts=3).generate_structured("prompt", Answer)
    assert inner.calls == 3
    assert len(slept.slept) == 2


def test_backoff_grows_and_stays_within_the_cap():
    inner, slept = Flaky(_connection_error(), failures=99), Recorder()
    with pytest.raises(groq.APIConnectionError):
        _client(inner, sleep=slept, attempts=5, max_sleep_seconds=4.0).generate_structured("prompt", Answer)
    assert slept.slept == [1.0, 2.0, 4.0, 4.0]


def test_jitter_keeps_concurrent_callers_from_retrying_in_lockstep():
    inner, slept = Flaky(_connection_error(), failures=99), Recorder()
    with pytest.raises(groq.APIConnectionError):
        RetryingLLMClient(inner, sleep=slept, attempts=3, jitter=lambda: 0.0).generate_structured("p", Answer)
    # Same schedule as above, halved by the low end of the jitter band.
    assert slept.slept == [0.5, 1.0]


def test_a_successful_call_never_sleeps():
    inner, slept = Flaky(_connection_error(), failures=0), Recorder()
    assert _client(inner, sleep=slept).generate_structured("prompt", Answer).value == "ok"
    assert inner.calls == 1 and slept.slept == []
