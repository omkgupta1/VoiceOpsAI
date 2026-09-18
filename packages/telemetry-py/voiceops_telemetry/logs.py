"""
Structured logging that carries the trace id.

The point of this module is one line in `configure()`: every log record gets the
current `trace_id` stamped on it. That is what turns "the worker logged an error
at 16:04:12" into "click this id and see the voice call that caused it" — without
it, traces and logs are two separate investigations of the same incident.

Local development gets a readable line, everything else gets JSON. Colour and
alignment matter when you are watching a terminal; they are noise to a log
shipper.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from voiceops_telemetry.tracing import current_ids

# Attributes logging puts on every record. Anything else was passed by the
# caller via `extra=` and belongs in the structured output.
_BUILTIN = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "thread", "threadName", "taskName",
}


class TraceFilter(logging.Filter):
    """Stamps the active trace and span id onto every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id, record.span_id = current_ids()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "service": os.environ.get("OTEL_SERVICE_NAME", "unknown"),
            "message": record.getMessage(),
        }
        if getattr(record, "trace_id", ""):
            payload["trace_id"] = record.trace_id
            payload["span_id"] = record.span_id
        for key, value in record.__dict__.items():
            if key not in _BUILTIN and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """The previous human-readable format, plus a short trace id."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        # Eight characters is enough to recognise a trace across two terminals
        # and short enough not to push the message off the line.
        short = getattr(record, "trace_id", "")[:8]
        return f"{base}  [{short}]" if short else base


def configure(service: str, *, level: str | None = None, json_output: bool | None = None) -> None:
    os.environ.setdefault("OTEL_SERVICE_NAME", service)

    if json_output is None:
        json_output = os.environ.get("LOG_FORMAT", "console").lower() == "json"

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(TraceFilter())
    handler.setFormatter(
        JsonFormatter() if json_output
        else ConsoleFormatter(
            fmt="%(asctime)s %(levelname)-5s %(name)-18s %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel((level or os.environ.get("LOG_LEVEL", "info")).upper())

    # These two narrate every request they serve or make; at INFO they bury the
    # lines we actually wrote.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


__all__ = ["configure", "TraceFilter", "JsonFormatter"]
