"""Retry a transient provider failure instead of losing the whole request.

The ten-dataset survey made this the most frequently-hit defect in the product (frontend/docs/hld.md
finding 27). In one pass over ten datasets, six journeys were derailed by transient Groq failures:
four lost schema discovery outright and one lost four of its five chat questions, every one to a
single 429 or "didn't respond" with nothing behind it. Re-running the same six, unchanged, passed
three immediately. Nothing was wrong with those datasets -- one unlucky call threw away an upload
that had already cost a file transfer and a full column profile.

Two things this deliberately does NOT do:

- **It does not wait out a daily quota.** Groq answers an exhausted per-day token budget with the
  same 429 class it uses for a per-minute burst, but a `retry-after` measured in minutes means the
  call cannot succeed within any sane request. A wait longer than `max_sleep_seconds` is treated as
  "don't bother", and the caller gets llm_errors' clean sentence immediately, exactly as before.
  Three separate keys hit the per-day ceiling during the survey; blocking a request thread for
  thirteen minutes would have been worse than failing.
- **It does not retry a request the provider rejected on its merits.** `llm_errors.is_transient` is
  the single definition of which failures are worth repeating; a 413 for an over-long prompt will be
  rejected identically every time.

Applied once at the wiring layer rather than inside each POC's own GroqLLMClient: all three clients
(schema discovery, dashboard, chat) implement the same one-method interface, and there are four
vendored copies of that client between them.
"""
import logging
import random
import time
from typing import Type, TypeVar

from pydantic import BaseModel

from insightflow_backend.llm_errors import is_transient, provider_message

logger = logging.getLogger("insightflow")

T = TypeVar("T", bound=BaseModel)

# Three attempts inside ~10s of sleep: enough for the blips the survey hit (a re-run minutes later
# passed three of six), short enough that a chat question stays interactive and a discovery job
# doesn't look hung.
_ATTEMPTS = 3
_BASE_DELAY_SECONDS = 1.0
_MAX_SLEEP_SECONDS = 8.0


def _retry_after_seconds(exc: BaseException) -> float | None:
    """The provider's own wait hint, if it sent a usable one."""
    response = getattr(exc, "response", None)
    header = response.headers.get("retry-after") if response is not None else None
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        # The RFC also allows an HTTP-date. Groq sends seconds; anything else we simply don't use.
        return None


class RetryingLLMClient:
    """Wraps any client with `generate_structured`, retrying only transient provider failures.

    Duck-typed rather than subclassing an LLMClient ABC: each POC vendors its own copy of that ABC,
    and consumers only ever call the one method.
    """

    def __init__(
        self,
        inner,
        *,
        attempts: int = _ATTEMPTS,
        max_sleep_seconds: float = _MAX_SLEEP_SECONDS,
        sleep=time.sleep,
        jitter=random.random,
    ):
        self._inner = inner
        self._attempts = attempts
        self._max_sleep_seconds = max_sleep_seconds
        self._sleep = sleep
        self._jitter = jitter

    def generate_structured(self, prompt: str, output_schema: Type[T]) -> T:
        for attempt in range(1, self._attempts + 1):
            try:
                return self._inner.generate_structured(prompt, output_schema)
            except Exception as exc:
                if attempt == self._attempts or not is_transient(exc):
                    raise
                delay = self._delay_before_retry(exc, attempt)
                if delay is None:
                    raise
                logger.warning(
                    "llm_call_retrying",
                    extra={
                        "attempt": attempt,
                        "of": self._attempts,
                        "sleeping_seconds": round(delay, 2),
                        "reason": provider_message(exc),
                    },
                )
                self._sleep(delay)
        raise AssertionError("unreachable: the final attempt either returns or raises")

    def _delay_before_retry(self, exc: BaseException, attempt: int) -> float | None:
        """Seconds to wait, or None if waiting can't help within this request."""
        hinted = _retry_after_seconds(exc)
        if hinted is not None:
            # A hint longer than we're willing to sleep means a quota window, not a blip.
            return hinted if hinted <= self._max_sleep_seconds else None
        # Exponential, with jitter so concurrent requests don't retry in lockstep.
        backoff = _BASE_DELAY_SECONDS * (2 ** (attempt - 1))
        return min(backoff, self._max_sleep_seconds) * (0.5 + 0.5 * self._jitter())
