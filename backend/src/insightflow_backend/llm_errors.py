"""One place that turns a provider exception into a sentence a tenant may read.

This used to live entirely in `main.py`'s FastAPI exception handlers, which is the right place for
half the callers and unreachable for the other half: schema discovery runs in the RQ worker, outside
the ASGI app, so `jobs.py` stored `str(exception)` on the `Job` row and `DatasetUpload` rendered it.
When Groq's daily token budget ran out during the ten-dataset survey, an Analyst who uploaded a CSV
was shown the provider's raw 429 body -- including this deployment's Groq organization id and a link
to the provider's billing page (frontend/docs/hld.md finding 28).

So the mapping lives here, the handlers call it, and the worker calls it too. Adding a provider
error class in one path can no longer leave the other path leaking the raw body.
"""
import groq

# A sentence per condition, and a flag for whether trying the same thing again could plausibly work.
# The wording matches what the HTTP handlers said before, so chat and dashboard read the same as they
# did; only discovery changes, from a raw provider dump to one of these.
_QUOTA = "The AI provider's usage limit has been reached. Try again in a few minutes."
_UNAVAILABLE = "The AI provider didn't respond. Try again in a moment."
_REJECTED = "The AI provider couldn't process this request. Try rephrasing or narrowing the question."


def provider_message(exc: BaseException) -> str | None:
    """The sentence to show for a provider failure, or None if `exc` isn't one.

    None means "not the provider's fault" -- the caller should fall back to its own reporting rather
    than pretend an unrelated bug was an outage.
    """
    if isinstance(exc, groq.RateLimitError):
        return _QUOTA
    if isinstance(exc, (groq.APIConnectionError, groq.InternalServerError)):
        return _UNAVAILABLE
    if isinstance(exc, groq.APIStatusError):
        return _REJECTED
    return None


def is_transient(exc: BaseException) -> bool:
    """Whether the same request could succeed later without being changed.

    A quota or an outage clears on its own; a request the provider rejected outright (413, a
    malformed prompt) will be rejected again. Callers use this to decide whether to invite a retry
    -- the discovery job says so in its error text rather than looking like a permanent failure.
    """
    return isinstance(exc, (groq.RateLimitError, groq.APIConnectionError, groq.InternalServerError))
