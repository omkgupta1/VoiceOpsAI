"""
Flight servicing tools — the operations from overview.md §4.

Each wraps one endpoint on the flight service and translates its error envelope
into a ToolResult. The descriptions and schemas here are prompt engineering as
much as code: they are the only thing the model reads when deciding what to call,
so they say when *not* to use a tool as much as when to.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

from app.config import settings
from app.tools.base import Tool, ToolResult, registry

# Shape checks per endpoint. The chaos engine's `corrupt` scenario returns
# HTTP 200 with a wrong-shaped body; without these, that sails through and fails
# further downstream where the cause is much harder to see.
_EXPECTED_KEYS: dict[str, set[str]] = {
    "status": {"flight_number", "status"},
    "booking": {"pnr", "status"},
    "cancel": {"pnr", "status"},
    "options": {"options"},
    "reschedule": {"pnr", "status"},
    "refund": {"reference", "status"},
    "refunds": {"refunds"},
}


async def _call(
    method: str, path: str, *, shape: str, json_body: dict | None = None
) -> ToolResult:
    """One HTTP call to the flight service, mapped onto a ToolResult."""
    url = f"{settings.flight_api_base_url.rstrip('/')}{path}"
    started = time.perf_counter()

    try:
        async with httpx.AsyncClient(timeout=settings.flight_api_timeout_sec) as client:
            response = await client.request(method, url, json=json_body)
    except httpx.TimeoutException:
        return ToolResult.failure(
            "UPSTREAM_TIMEOUT",
            f"The flight service did not respond within {settings.flight_api_timeout_sec}s",
            retryable=True,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except httpx.HTTPError as exc:
        return ToolResult.failure(
            "CONNECTION_FAILED", f"Could not reach the flight service: {exc}",
            retryable=True, duration_ms=int((time.perf_counter() - started) * 1000),
        )

    elapsed_ms = int((time.perf_counter() - started) * 1000)

    if response.status_code >= 400:
        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}
        return ToolResult.failure(
            error.get("code") or f"HTTP_{response.status_code}",
            error.get("message") or response.text[:200],
            # Trust the flag when present, but fall back to the status code —
            # the Phase 6 engine must work against upstreams that offer neither.
            retryable=bool(error.get("retryable", response.status_code >= 500
                                     or response.status_code == 429)),
            duration_ms=elapsed_ms,
        )

    try:
        body = response.json()
    except ValueError:
        return ToolResult.failure(
            "MALFORMED_RESPONSE", "The flight service returned a body that is not JSON",
            retryable=True, duration_ms=elapsed_ms,
        )

    expected = _EXPECTED_KEYS.get(shape, set())
    if not isinstance(body, dict) or not expected.issubset(body):
        missing = expected - set(body) if isinstance(body, dict) else expected
        return ToolResult.failure(
            "MALFORMED_RESPONSE",
            f"The flight service returned an unexpected shape (missing: {', '.join(sorted(missing))})",
            # A corrupt body is a symptom of a sick upstream, not a bad request,
            # so it is worth another attempt.
            retryable=True,
            duration_ms=elapsed_ms,
        )

    return ToolResult.success(body, duration_ms=elapsed_ms)


# Everything a speech-to-text pass tends to sprinkle into an identifier:
# spaces, hyphens, dots, and the occasional trailing full stop.
_NOISE = re.compile(r"[\s\-.,_]+")


def _normalise_identifier(value: str) -> str:
    """
    Strip transcription noise out of a flight number or booking reference.

    Speech recognition reliably inserts a space into alphanumerics: "AI858"
    comes back as "AI 858", and an exact-match lookup then fails on a flight
    that exists. Measured on both whisper models, so it is a property of the
    task rather than of one model size.

    This belongs here, at the boundary where noisy speech meets a structured
    identifier, rather than in the flight service — which is a backend API and
    should stay strict about what it accepts.
    """
    return _NOISE.sub("", (value or "")).upper()


# ---------- Handlers ----------


async def check_flight_status(flight_number: str) -> ToolResult:
    number = _normalise_identifier(flight_number)
    return await _call("GET", f"/v1/flights/{number}/status", shape="status")


async def get_booking(pnr: str) -> ToolResult:
    return await _call("GET", f"/v1/bookings/{_normalise_identifier(pnr)}", shape="booking")


async def cancel_booking(pnr: str) -> ToolResult:
    return await _call(
        "POST", f"/v1/bookings/{_normalise_identifier(pnr)}/cancel", shape="cancel"
    )


async def get_reschedule_options(pnr: str) -> ToolResult:
    return await _call(
        "GET", f"/v1/bookings/{_normalise_identifier(pnr)}/reschedule-options", shape="options"
    )


async def reschedule_booking(pnr: str, flight_number: str) -> ToolResult:
    """
    Move a booking to another flight, identified the way the conversation does.

    The API wants an opaque flight id, but the agent reads *flight numbers* out
    loud — "SG584 departs at eight forty-five" — so that is what the model has in
    hand when the customer says "the first one". Asking it for an id it never
    spoke is asking it to fail, and it did: the first live reschedule passed
    "SG584" as the id and crashed the flight service.

    So the tool takes the flight number and resolves it, re-fetching the options
    to find the matching id. One extra call, one fewer way to be wrong.
    """
    booking = _normalise_identifier(pnr)
    wanted = _normalise_identifier(flight_number)

    options = await _call(
        "GET", f"/v1/bookings/{booking}/reschedule-options", shape="options"
    )
    if not options.ok:
        return options

    match = next(
        (
            option
            for option in options.data.get("options", [])
            if _normalise_identifier(option.get("flight_number", "")) == wanted
        ),
        None,
    )
    if match is None:
        available = ", ".join(
            o.get("flight_number", "?") for o in options.data.get("options", [])
        )
        return ToolResult.failure(
            "FLIGHT_NOT_AN_OPTION",
            f"{flight_number} is not one of the alternatives for {booking}. "
            f"Available: {available or 'none'}",
        )

    return await _call(
        "POST", f"/v1/bookings/{booking}/reschedule", shape="reschedule",
        json_body={"flight_id": match["flight_id"]},
    )


async def check_refund_status(pnr: str) -> ToolResult:
    return await _call(
        "GET", f"/v1/bookings/{_normalise_identifier(pnr)}/refunds", shape="refunds"
    )


async def escalate_to_human(reason: str) -> ToolResult:
    """
    Hand the conversation to a person.

    Deliberately always succeeds: escalation is the fallback for everything else
    going wrong, so it must never itself be a thing that can fail. The Phase 7
    API turns this into a real assignment; here it records the decision.
    """
    return ToolResult.success({"escalated": True, "reason": reason})


# ---------- Registration ----------

_PNR = {
    "type": "string",
    "description": "The six-character booking reference, e.g. 7MGFXC",
}

registry.register(Tool(
    name="check_flight_status",
    description=(
        "Get the current status of a flight by its flight number, including delays, "
        "gate and terminal. Use when the customer asks whether a flight is on time, "
        "delayed or cancelled. Requires only a flight number, not a booking."
    ),
    parameters={
        "type": "object",
        "properties": {
            "flight_number": {
                "type": "string",
                "description": "The flight number, e.g. AI302 or 6E455",
            }
        },
        "required": ["flight_number"],
    },
    handler=check_flight_status,
))

registry.register(Tool(
    name="get_booking",
    description=(
        "Look up a booking by its six-character reference (PNR). Call this first for "
        "ANY request about an existing booking - cancelling, rescheduling, refunds, or "
        "checking details - so you can confirm the details back to the customer before "
        "acting."
    ),
    parameters={"type": "object", "properties": {"pnr": _PNR}, "required": ["pnr"]},
    handler=get_booking,
))

registry.register(Tool(
    name="cancel_booking",
    description=(
        "Cancel a booking and start a refund if the fare is refundable. "
        "This cannot be undone. Only call it after the customer has explicitly "
        "confirmed they want to cancel that specific booking."
    ),
    parameters={"type": "object", "properties": {"pnr": _PNR}, "required": ["pnr"]},
    handler=cancel_booking,
    mutating=True,
))

registry.register(Tool(
    name="get_reschedule_options",
    description=(
        "List alternative flights a booking can be moved to, on the same route. "
        "Call this before rescheduling so the customer can choose, and read the "
        "options back to them."
    ),
    parameters={"type": "object", "properties": {"pnr": _PNR}, "required": ["pnr"]},
    handler=get_reschedule_options,
))

registry.register(Tool(
    name="reschedule_booking",
    description=(
        "Move a booking to a different flight on the same route. Only call after "
        "the customer has chosen one of the options from get_reschedule_options "
        "and confirmed it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pnr": _PNR,
            "flight_number": {
                "type": "string",
                "description": "The flight number the customer chose, e.g. SG584",
            },
        },
        "required": ["pnr", "flight_number"],
    },
    handler=reschedule_booking,
    mutating=True,
))

registry.register(Tool(
    name="check_refund_status",
    description=(
        "Get the status of any refunds for a booking. Use when the customer asks "
        "where their money is or whether a refund has been processed."
    ),
    parameters={"type": "object", "properties": {"pnr": _PNR}, "required": ["pnr"]},
    handler=check_refund_status,
))

registry.register(Tool(
    name="escalate_to_human",
    # Phrased to lead with the trigger words a customer actually uses. The
    # abstract version ("hand the conversation to a human agent") was not matched
    # against "put me through to a real person"; this one is. The closing sentence
    # exists because the model would otherwise say it was transferring the call
    # without calling anything.
    description=(
        "Transfer the call to a human agent. Call this whenever the customer asks for "
        "a person, a human, a real agent, a manager, or to be put through to someone. "
        "Also call it when you cannot complete their request, or when you are unsure "
        "and acting anyway could be harmful. You must call this tool to actually "
        "transfer the call - saying you will transfer them does nothing on its own."
    ),
    parameters={
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "A short explanation of why a human is needed",
            }
        },
        "required": ["reason"],
    },
    handler=escalate_to_human,
))


# ---------- Live external data ----------
#
# These reach genuinely outside the system: real weather observations and real
# aircraft transponder positions. Both are proxied through the flight service so
# the caching and rate-limit handling live in one place.
#
# They fail for reasons nobody here controls, which makes them the most honest
# retry material in the project — a chaos scenario is a simulation of this.

_EXPECTED_KEYS["weather"] = {"airport", "temperature_c"}
_EXPECTED_KEYS["aircraft"] = {"flight_number", "airborne"}


async def check_airport_weather(airport_code: str) -> ToolResult:
    code = _normalise_identifier(airport_code)
    return await _call("GET", f"/v1/live/weather/{code}", shape="weather")


async def check_aircraft_position(flight_number: str) -> ToolResult:
    number = _normalise_identifier(flight_number)
    return await _call("GET", f"/v1/live/aircraft/{number}", shape="aircraft")


registry.register(Tool(
    name="check_airport_weather",
    description=(
        "Get the current weather at an airport, using its three-letter code like DEL "
        "or BOM. Call this when the customer asks about weather, or whether weather "
        "might delay or has delayed a flight. Returns temperature, wind, visibility "
        "and whether conditions are likely to cause delays."
    ),
    parameters={
        "type": "object",
        "properties": {
            "airport_code": {
                "type": "string",
                "description": "The three-letter airport code, e.g. DEL for Delhi",
            }
        },
        "required": ["airport_code"],
    },
    handler=check_airport_weather,
))

registry.register(Tool(
    name="check_aircraft_position",
    description=(
        "Find out where an aircraft is right now — whether it is in the air, its "
        "altitude and speed. Call this when the customer asks where their plane is, "
        "whether it has taken off, or whether it is still flying. This is live "
        "position data, not the schedule; use check_flight_status for delays."
    ),
    parameters={
        "type": "object",
        "properties": {
            "flight_number": {
                "type": "string",
                "description": "The flight number, e.g. AI302 or 6E455",
            }
        },
        "required": ["flight_number"],
    },
    handler=check_aircraft_position,
))
