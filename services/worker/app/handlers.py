"""
Job handlers.

A handler does the work and either returns a result or raises `JobFailure`
carrying enough information for the retry engine to classify it. Handlers never
decide their own retry policy — that belongs in one place, so that the rules are
the same for every kind of work.

These call the flight service directly rather than going through the AI service.
The AI service guards mutations behind the confirmation gate (ADR 0004), and an
"execute any tool" endpoint for the worker to call would be a hole straight
through it. A retry job is only ever created for an operation the customer
already confirmed, so the consent exists — it was given during the call, and
re-running the same operation honours it rather than bypassing it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

_NOISE = re.compile(r"[\s\-.,_]+")


def _clean(value: str) -> str:
    return _NOISE.sub("", value or "").upper()


@dataclass(slots=True)
class JobFailure(Exception):
    """A handler failure, described well enough to be classified."""

    message: str
    code: str | None = None
    status_code: int | None = None
    retryable_hint: bool | None = None
    service: str = "unknown"


async def _flight_call(method: str, path: str, json_body: dict | None = None) -> dict[str, Any]:
    url = f"{settings.flight_api_base_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=settings.flight_api_timeout_sec) as client:
            response = await client.request(method, url, json=json_body)
    except httpx.TimeoutException as exc:
        raise JobFailure(
            f"flight service did not respond within {settings.flight_api_timeout_sec}s",
            code="UPSTREAM_TIMEOUT", service="flight-api",
        ) from exc
    except httpx.HTTPError as exc:
        raise JobFailure(
            f"could not reach the flight service: {exc}",
            code="CONNECTION_FAILED", service="flight-api",
        ) from exc

    if response.status_code >= 400:
        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}
        raise JobFailure(
            error.get("message") or response.text[:200],
            code=error.get("code"),
            status_code=response.status_code,
            retryable_hint=error.get("retryable"),
            service="flight-api",
        )

    try:
        return response.json()
    except ValueError as exc:
        raise JobFailure(
            "flight service returned a body that is not JSON",
            code="MALFORMED_RESPONSE", service="flight-api",
        ) from exc


# ---------- Handlers ----------


async def cancel_booking(payload: dict[str, Any]) -> dict[str, Any]:
    """Complete a cancellation the customer confirmed but the call could not finish."""
    pnr = _clean(payload.get("pnr", ""))
    if not pnr:
        # Nothing to retry: a job with no booking reference will never succeed.
        raise JobFailure("no pnr in payload", code="INVALID_ARGUMENTS", service="worker")

    result = await _flight_call("POST", f"/v1/bookings/{pnr}/cancel")
    return {"pnr": pnr, "status": result.get("status"), "refund": result.get("refund")}


async def reschedule_booking(payload: dict[str, Any]) -> dict[str, Any]:
    pnr = _clean(payload.get("pnr", ""))
    wanted = _clean(payload.get("flight_number", ""))
    if not pnr or not wanted:
        raise JobFailure(
            "pnr and flight_number are both required",
            code="INVALID_ARGUMENTS", service="worker",
        )

    options = await _flight_call("GET", f"/v1/bookings/{pnr}/reschedule-options")
    match = next(
        (o for o in options.get("options", []) if _clean(o.get("flight_number", "")) == wanted),
        None,
    )
    if match is None:
        # The flight the customer chose is gone. Retrying cannot bring it back;
        # this needs a person, so it must not burn four more attempts first.
        raise JobFailure(
            f"{wanted} is no longer an option for {pnr}",
            code="FLIGHT_NOT_AN_OPTION", service="flight-api",
        )

    result = await _flight_call(
        "POST", f"/v1/bookings/{pnr}/reschedule", {"flight_id": match["flight_id"]}
    )
    return {"pnr": pnr, "status": result.get("status"), "flight_number": wanted}


async def check_refund_status(payload: dict[str, Any]) -> dict[str, Any]:
    pnr = _clean(payload.get("pnr", ""))
    if not pnr:
        raise JobFailure("no pnr in payload", code="INVALID_ARGUMENTS", service="worker")
    return await _flight_call("GET", f"/v1/bookings/{pnr}/refunds")


async def scheduled_callback(payload: dict[str, Any]) -> dict[str, Any]:
    """
    A callback that is due.

    Placing the outbound call needs telephony, which this project does not have
    yet. Until then the job records that the callback came due, which is enough
    to exercise scheduling, priority and the retry path end to end.
    """
    return {
        "callback_due": True,
        "customer_id": payload.get("customer_id"),
        "reason": payload.get("reason", "scheduled callback"),
    }


HANDLERS = {
    "cancel_booking": cancel_booking,
    "reschedule_booking": reschedule_booking,
    "check_refund_status": check_refund_status,
    "scheduled_callback": scheduled_callback,
}
