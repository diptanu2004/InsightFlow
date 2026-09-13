"""Request-scoped correlation IDs for structured logging. `get_current_user`/`require_org_role`/
`require_project_role` populate these as soon as they resolve who's calling and which org is in
scope; the request-logging middleware in main.py reads them back after the route handler
finishes, so one log line per request carries user/org context even though the middleware itself
runs before those dependencies do.

ContextVars, not a plain module-level dict: this process handles requests concurrently (async
route handlers, threadpooled sync ones), and a global mutable dict would let one request's
user_id bleed into another's log line under real concurrency.
"""
import uuid
from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)
_org_id: ContextVar[str | None] = ContextVar("org_id", default=None)


def new_request_id() -> str:
    request_id = str(uuid.uuid4())
    _request_id.set(request_id)
    return request_id


def set_user_id(user_id: uuid.UUID | str) -> None:
    _user_id.set(str(user_id))


def set_org_id(org_id: uuid.UUID | str) -> None:
    _org_id.set(str(org_id))


def get_request_id() -> str | None:
    return _request_id.get()


def get_user_id() -> str | None:
    return _user_id.get()


def get_org_id() -> str | None:
    return _org_id.get()
