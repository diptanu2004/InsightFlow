"""One JSON-line-per-request log format, configured once at process startup. Plain `logging`
with no custom formatter silently drops everything passed via `extra=` -- without this, main.py's
request-logging middleware would format its request_id/user_id/org_id fields into nothing.
"""
import json
import logging

_EXTRA_FIELDS = ("request_id", "method", "path", "status_code", "duration_ms", "user_id", "org_id")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
