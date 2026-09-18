"""
Trace context across the queue.

The queue package must keep working without the telemetry package installed —
`test_queue.py` runs against a bare Redis with no observability at all, and a
library that cannot be tested in isolation is a library that stops being tested.
So the import is optional and both functions degrade to doing nothing.
"""

from __future__ import annotations

from typing import Any, Mapping

try:  # pragma: no cover - exercised by whichever half is installed
    from voiceops_telemetry.tracing import carrier as _carrier
    from voiceops_telemetry.tracing import context_from as _context_from
except ImportError:  # telemetry not installed: the queue still works
    def _carrier() -> dict[str, str]:
        return {}

    def _context_from(headers: Mapping[str, str] | None) -> Any:
        return None


def current_carrier() -> dict[str, str]:
    """W3C headers for the trace this enqueue is happening inside."""
    return _carrier()


def parent_context(headers: Mapping[str, str] | None) -> Any:
    """The context a job was enqueued under, for use as an attempt's parent."""
    return _context_from(headers)


__all__ = ["current_carrier", "parent_context"]
