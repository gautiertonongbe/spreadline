"""Structured logging with credential redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

from app.core.config import settings

#: Patterns whose values never reach a log sink. Provider credentials and tokens
#: routinely appear inside request URLs and exception reprs.
_REDACT_KEYS = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|authorization|credential|access[_-]?key|refresh[_-]?token)"
)
_REDACTED = "[redacted]"


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (_REDACTED if _REDACT_KEYS.search(str(key)) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
        }
        extra = getattr(record, "context", None)
        if extra:
            payload["context"] = redact(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s :: %(message)s")
        )
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.database_echo else logging.WARNING
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
