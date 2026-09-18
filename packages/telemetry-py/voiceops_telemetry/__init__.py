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
    setup as setup_tracing,
    span,
    tracer,
)


def setup(service: str, *, metrics_port: int | None = None) -> None:
    """Tracing, logging and (optionally) a metrics endpoint, in one call."""
    setup_tracing(service)
    configure_logging(service)
    if metrics_port:
        metrics.serve(metrics_port)


__all__ = [
    "setup", "setup_tracing", "configure_logging", "instrument_fastapi",
    "span", "tracer", "fail", "current_ids", "current_span", "carrier", "context_from",
    "SpanKind", "metrics",
]
