"""
Prometheus metrics.

Every metric is declared here rather than beside the code that records it. Metric
names are a public interface — dashboards and alerts are written against them —
and a name that drifts breaks a dashboard silently, weeks later. One file can be
read in full before adding a fifth variant of the same counter.

Traces answer "what happened in this call". Metrics answer "is this normal", and
that question needs every call, not the ones that happened to be sampled.
"""

from __future__ import annotations

from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    make_asgi_app,
)

# Latency buckets are chosen for this system, not copied from a template. A local
# 7B model answers in 5-10s, so the default buckets (which top out at 10s) would
# put almost every LLM call in the last bucket and make p95 meaningless.
VOICE_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 3, 5, 7.5, 10, 15, 20, 30, 60)
TOOL_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
JOB_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300)

# ---------------------------------------------------------------- voice

voice_stage_seconds = Histogram(
    "voiceops_voice_stage_seconds",
    "Time spent in one stage of the voice pipeline.",
    ["stage", "provider"],
    buckets=VOICE_BUCKETS,
)

turns_total = Counter(
    "voiceops_turns_total",
    "Conversation turns completed, by how they ended.",
    ["outcome"],  # answered | escalated | failed
)

tool_calls_total = Counter(
    "voiceops_tool_calls_total",
    "Tool invocations by the model.",
    ["tool", "outcome"],  # ok | error | blocked
)

tool_duration_seconds = Histogram(
    "voiceops_tool_duration_seconds",
    "Time for one tool call, including the downstream HTTP request.",
    ["tool"],
    buckets=TOOL_BUCKETS,
)

confirmation_gate_total = Counter(
    "voiceops_confirmation_gate_total",
    "Mutating tool calls the confirmation gate stopped, by reason.",
    ["reason"],
)

# ---------------------------------------------------------------- queue

queue_depth = Gauge(
    "voiceops_queue_depth",
    "Jobs waiting in the queue right now.",
    ["priority", "state"],  # state: ready | scheduled | processing | dead_letter
)

jobs_total = Counter(
    "voiceops_jobs_total",
    "Job attempts that reached a terminal outcome.",
    ["job_type", "outcome"],  # success | failed | retry | dead_letter
)

job_duration_seconds = Histogram(
    "voiceops_job_duration_seconds",
    "Time to run one job attempt.",
    ["job_type"],
    buckets=JOB_BUCKETS,
)

job_backoff_seconds = Histogram(
    "voiceops_job_backoff_seconds",
    "Delay applied before a retry — the backoff curve, as measured.",
    ["job_type"],
    buckets=JOB_BUCKETS,
)

job_attempts = Histogram(
    "voiceops_job_attempts",
    "How many attempts a job needed before it stopped being retried.",
    ["job_type", "outcome"],
    buckets=(1, 2, 3, 4, 5, 6, 8, 10),
)

# ---------------------------------------------------------------- chaos

chaos_injections_total = Counter(
    "voiceops_chaos_injections_total",
    "Failures the chaos engine injected deliberately, by kind.",
    ["kind"],
)

chaos_requests_total = Counter(
    "voiceops_chaos_requests_total",
    "Requests the flight service saw, split by whether chaos touched them.",
    ["outcome"],  # passed | injected
)


# ---------------------------------------------------------------- downstream

upstream_errors_total = Counter(
    "voiceops_upstream_errors_total",
    "Errors from a downstream service, split by whether we retried them.",
    ["service", "error_class", "code"],  # error_class: RETRYABLE | PERMANENT
)


def install(app: Any) -> None:
    """
    Add `GET /metrics` to a FastAPI app.

    A plain route rather than `app.mount("/metrics", ...)`: a mount answers the
    un-slashed path with a 307 to `/metrics/`, so every scrape — once every five
    seconds, forever — would pay for a redirect before getting any data.
    """
    from fastapi import Response

    @app.get("/metrics", include_in_schema=False)
    def _metrics() -> Response:  # pragma: no cover - trivial
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def asgi_app():
    """The raw ASGI app, for anything that is not FastAPI."""
    return make_asgi_app()


def serve(port: int) -> None:
    """Expose /metrics from a process that is not already an HTTP server.

    The worker has no web server of its own, and Prometheus only scrapes — it is
    never pushed to — so the worker has to open a port purely to be scraped.
    """
    from prometheus_client import start_http_server

    start_http_server(port)


__all__ = [
    "voice_stage_seconds", "turns_total", "tool_calls_total", "tool_duration_seconds",
    "confirmation_gate_total", "chaos_injections_total", "chaos_requests_total", "queue_depth", "jobs_total", "job_duration_seconds",
    "job_backoff_seconds", "job_attempts", "upstream_errors_total",
    "install", "asgi_app", "serve", "CollectorRegistry",
]
