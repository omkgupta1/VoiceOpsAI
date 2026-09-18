"""
The chaos engine.

Deliberately breaks this service so the retry, backoff and remediation machinery
built in later phases has something real to react to. Without it, the reliability
half of this project could never be exercised or demonstrated.

State lives in memory and is changed at runtime through /admin/chaos — no restart,
so you can break the service while watching a queue drain and see what happens.
"""

from __future__ import annotations

import asyncio
import random
from typing import Literal

from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from voiceops_telemetry import metrics

from app.config import settings

ErrorKind = Literal["503", "429", "500", "504", "timeout", "malformed"]

# What each error kind turns into on the wire. `timeout` and `malformed` are
# deliberately nastier than a clean 5xx: they produce a hung connection and a
# valid-status-but-broken-body, which is how real services actually misbehave
# and what naive clients handle worst.
_ERROR_RESPONSES: dict[str, tuple[int, str, str, bool]] = {
    "503": (503, "SERVICE_UNAVAILABLE", "Flight service is temporarily unavailable", True),
    "429": (429, "RATE_LIMITED", "Too many requests to the flight service", True),
    "500": (500, "INTERNAL_ERROR", "Unexpected failure in the flight service", True),
    "504": (504, "UPSTREAM_TIMEOUT", "Flight service did not respond in time", True),
}


class ChaosConfig(BaseModel):
    enabled: bool = False
    # Probability in [0, 1] that any given request fails.
    error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    # Artificial delay added to every request before it is served.
    latency_ms: int = Field(default=0, ge=0)
    latency_jitter_ms: int = Field(default=0, ge=0)
    error_kind: ErrorKind = "503"
    # How long `timeout` hangs for, to trip a client's own read timeout.
    timeout_hang_ms: int = Field(default=30_000, ge=0)
    # Per-path overrides, so one endpoint can be broken while the rest stay healthy.
    # Keys are path prefixes, e.g. "/v1/bookings".
    endpoints: dict[str, "ChaosConfig"] = Field(default_factory=dict)


ChaosConfig.model_rebuild()


# Named profiles, so a demo or a test can ask for a behaviour rather than
# assembling one knob at a time.
SCENARIOS: dict[str, ChaosConfig] = {
    "healthy": ChaosConfig(enabled=False),
    "flaky": ChaosConfig(enabled=True, error_rate=0.4, error_kind="503"),
    "hard_down": ChaosConfig(enabled=True, error_rate=1.0, error_kind="503"),
    "slow": ChaosConfig(enabled=True, error_rate=0.0, latency_ms=2500, latency_jitter_ms=1500),
    "rate_limited": ChaosConfig(enabled=True, error_rate=0.7, error_kind="429"),
    "timeouts": ChaosConfig(enabled=True, error_rate=0.5, error_kind="timeout", timeout_hang_ms=8000),
    "corrupt": ChaosConfig(enabled=True, error_rate=0.5, error_kind="malformed"),
}


class ChaosState:
    """In-memory chaos configuration, mutated through the admin API."""

    def __init__(self) -> None:
        self.config = ChaosConfig(
            enabled=settings.chaos_enabled,
            error_rate=settings.chaos_error_rate,
            latency_ms=settings.chaos_latency_ms,
            error_kind=settings.chaos_error_kind,  # type: ignore[arg-type]
        )
        # Counters, so you can prove what the engine actually did.
        self.injected = 0
        self.passed = 0

    def resolve(self, path: str) -> ChaosConfig:
        """Most specific endpoint override wins; otherwise the global config."""
        matches = [p for p in self.config.endpoints if path.startswith(p)]
        if matches:
            return self.config.endpoints[max(matches, key=len)]
        return self.config

    def reset_counters(self) -> None:
        self.injected = 0
        self.passed = 0


state = ChaosState()


class ChaosMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # The admin and health surfaces must never be broken — otherwise you
        # cannot turn chaos back off, and `hard_down` becomes unrecoverable.
        if path.startswith("/admin") or path in ("/health", "/docs", "/openapi.json"):
            return await call_next(request)

        config = state.resolve(path)
        if not config.enabled:
            return await call_next(request)

        if config.latency_ms or config.latency_jitter_ms:
            jitter = random.uniform(0, config.latency_jitter_ms) if config.latency_jitter_ms else 0
            await asyncio.sleep((config.latency_ms + jitter) / 1000)

        if random.random() >= config.error_rate:
            state.passed += 1
            metrics.chaos_requests_total.labels("passed").inc()
            return await call_next(request)

        state.injected += 1
        # Exported so a retry spike on the dashboard can be read next to the
        # thing that caused it. A backoff curve climbing with no injection
        # alongside it means something is failing for real.
        metrics.chaos_requests_total.labels("injected").inc()
        metrics.chaos_injections_total.labels(config.error_kind).inc()
        return await _inject(config)


async def _inject(config: ChaosConfig) -> JSONResponse:
    kind = config.error_kind

    if kind == "timeout":
        # Hang rather than answer. The client's own read timeout must fire —
        # which is the failure mode naive clients handle worst.
        await asyncio.sleep(config.timeout_hang_ms / 1000)
        status, code, message, retryable = _ERROR_RESPONSES["504"]
        return JSONResponse(
            status_code=status,
            content={"error": {"code": code, "message": message, "retryable": retryable}},
            headers={"x-chaos-injected": "timeout"},
        )

    if kind == "malformed":
        # HTTP 200 with a body that does not match the schema. Status-code-only
        # error handling sails straight past this, which is the point.
        return JSONResponse(
            status_code=200,
            content={"unexpected": "shape", "data": None},
            headers={"x-chaos-injected": "malformed"},
        )

    status, code, message, retryable = _ERROR_RESPONSES[kind]
    headers = {"x-chaos-injected": kind}
    if kind == "429":
        headers["retry-after"] = "2"
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "retryable": retryable}},
        headers=headers,
    )
