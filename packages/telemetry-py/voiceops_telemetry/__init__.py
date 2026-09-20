"""Shared observability for the Python services: traces, logs and metrics."""

from voiceops_telemetry import metrics
from voiceops_telemetry.logs import configure as configure_logging
from voiceops_telemetry.tracing import (
    SpanKind,
    carrier,
    context_from,
    current_ids,
    current_span,
    fail,
    instrument_fastapi,
    span,
    tracer,
)
from voiceops_telemetry.tracing import (
    setup as setup_tracing,
)


def setup(service: str, *, metrics_port: int | None = None) -> None:
    """Tracing, logging and (optionally) a metrics endpoint, in one call."""
    setup_tracing(service)
    configure_logging(service)
    if metrics_port:
        metrics.serve(metrics_port)


__all__ = [
    "SpanKind",
    "carrier",
    "configure_logging",
    "context_from",
    "current_ids",
    "current_span",
    "fail",
    "instrument_fastapi",
    "metrics",
    "setup",
    "setup_tracing",
    "span",
    "tracer",
]
