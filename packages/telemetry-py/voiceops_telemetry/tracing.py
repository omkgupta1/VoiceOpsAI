"""
Tracing setup, shared by the AI service, the worker and the flight service.

One `setup()` call per process. Auto-instrumentation covers the plumbing — HTTP
in and out, SQL, Redis commands — and the spans worth naming are written by hand
where they happen. That split is deliberate: the auto spans are the ones nobody
learns anything from writing, and the manual ones are the ones that carry this
project's meaning (which stage of the voice pipeline, which tool, why a job was
retried).

Propagation across the queue is the interesting part and lives in `carrier()` /
`context_from()` at the bottom of this file.
"""

from __future__ import annotations

import os
from typing import Any, Iterator, Mapping
from contextlib import contextmanager

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

_configured = False


def setup(service: str, *, version: str = "0.1.0") -> None:
    """
    Install the global tracer provider. Safe to call more than once.

    Exporting is best-effort by design. If Jaeger is not running the SDK drops
    spans and logs a warning, and the service keeps serving — observability that
    can take production down is worse than no observability.
    """
    global _configured
    if _configured:
        return

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    provider = TracerProvider(
        resource=Resource.create({
            "service.name": service,
            "service.version": version,
            "deployment.environment": os.environ.get("ENV", "local"),
        })
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
    )
    trace.set_tracer_provider(provider)

    _instrument_libraries()
    _configured = True


def _instrument_libraries() -> None:
    """
    Patch the clients we use, each guarded on its own.

    The import has to be inside the guard, not above it. Each of these
    instrumentation packages imports the library it patches at module level, so
    `from opentelemetry.instrumentation.redis import ...` raises ImportError in a
    service that has no redis installed — and a single import list would take the
    other two down with it. The flight service does exactly that: it speaks HTTP
    and Postgres, never Redis.
    """
    for module, attribute in (
        ("opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor"),
        ("opentelemetry.instrumentation.psycopg", "PsycopgInstrumentor"),
        ("opentelemetry.instrumentation.redis", "RedisInstrumentor"),
    ):
        try:
            imported = __import__(module, fromlist=[attribute])
            getattr(imported, attribute)().instrument()
        except Exception:  # noqa: BLE001 - telemetry must never block startup
            pass


def instrument_fastapi(app: Any) -> None:
    """Server spans for every request, plus the route template as the span name."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    try:
        # /health and /metrics are polled constantly and would drown the traces
        # of actual work in a view that is meant to be read by a human.
        FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")
    except Exception:  # noqa: BLE001
        pass


def tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)


@contextmanager
def span(
    name: str,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Mapping[str, Any] | None = None,
    context: otel_context.Context | None = None,
) -> Iterator[Span]:
    """
    A span that records the exception and marks itself failed, then re-raises.

    The default `record_exception` leaves the span's status UNSET, so a trace
    full of failures still shows every span green. Getting that wrong makes the
    whole trace view lie, which is worse than not having it.

    Passing `context` parents the span somewhere other than the current stack —
    which is how a job attempt running minutes later, in another process, still
    belongs to the trace of the call that queued it.
    """
    with trace.get_tracer("voiceops").start_as_current_span(
        name, kind=kind, context=context
    ) as current:
        if attributes:
            for key, value in attributes.items():
                if value is not None:
                    current.set_attribute(key, value)
        try:
            yield current
        except Exception as exc:
            current.record_exception(exc)
            current.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def fail(current: Span, message: str, *, code: str | None = None) -> None:
    """Mark a span failed for an error that was returned rather than raised.

    Tool failures and job failures travel as values in this codebase, so nothing
    is raised for the span context manager to catch.
    """
    current.set_status(Status(StatusCode.ERROR, message))
    if code:
        current.set_attribute("error.code", code)


def current_span() -> Span:
    """The span this code is running inside — usually the auto-created server span."""
    return trace.get_current_span()


def current_ids() -> tuple[str, str]:
    """`(trace_id, span_id)` as hex, or empty strings outside a span."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return "", ""
    return format(context.trace_id, "032x"), format(context.span_id, "016x")


# ---------------------------------------------------------------- propagation

def carrier() -> dict[str, str]:
    """
    The current trace context as W3C headers, for putting inside a queue job.

    HTTP propagation is automatic; a Redis job is just a hash of strings, so the
    context has to be written into the job explicitly and read back out when a
    worker picks it up — possibly seconds or minutes later, in another process.
    """
    headers: dict[str, str] = {}
    inject(headers)
    return headers


def context_from(headers: Mapping[str, str] | None) -> otel_context.Context | None:
    """Rebuild the context a job was enqueued under. None if it carries none."""
    if not headers:
        return None
    return extract(dict(headers))


__all__ = [
    "setup", "instrument_fastapi", "tracer", "span", "fail", "current_ids", "current_span",
    "carrier", "context_from", "SpanKind", "Status", "StatusCode",
]
