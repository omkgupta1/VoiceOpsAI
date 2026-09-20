"""
The confirmation gate.

The one safety property in this service: a booking must never be changed without
the customer having heard its details and then agreed. It exists because the
orchestrator once cancelled a real booking on the first turn, having looked it up
and cancelled it in a single breath (ADR 0004).

Two conditions must both hold before a mutating tool runs:

  1. the booking's details were read back on an earlier turn of this call, and
  2. no other tool has run yet this turn.

Either alone is insufficient. (1) without (2) lets the model look up and cancel
in one breath; (2) without (1) lets it cancel a booking never described to anyone.
"""

from __future__ import annotations

import pytest
from app.orchestrator.turn import run_turn


async def test_lookup_and_cancel_in_one_turn_is_blocked(scripted, call, blocked, executed) -> None:
    """The original bug, as a regression test."""
    result = await run_turn(
        "cancel my booking ABC123",
        llm=scripted(
            [call("get_booking", pnr="ABC123"), call("cancel_booking", pnr="ABC123")],
            "Cancelled.",
        ),
    )

    assert "cancel_booking" in blocked(result), f"it executed: {executed(result)}"


async def test_cancel_without_any_lookup_is_blocked(scripted, call, blocked) -> None:
    """However insistent the customer's phrasing, consent is not a word match."""
    result = await run_turn(
        "cancel ABC123 immediately, I confirm",
        llm=scripted([call("cancel_booking", pnr="ABC123")], "Cancelled."),
    )

    assert "cancel_booking" in blocked(result)


async def test_cancel_after_a_prior_readback_is_allowed(scripted, call, executed, blocked) -> None:
    """The legitimate path — informed on an earlier turn, asked for on this one."""
    result = await run_turn(
        "yes, cancel it",
        llm=scripted([call("cancel_booking", pnr="ABC123")], "Cancelled."),
        informed_bookings={"ABC123"},
    )

    assert "cancel_booking" in executed(result), f"it was blocked: {blocked(result)}"


async def test_consent_is_per_booking_not_per_call(scripted, call, blocked) -> None:
    result = await run_turn(
        "actually cancel XYZ789 too",
        llm=scripted([call("cancel_booking", pnr="XYZ789")], "Cancelled."),
        informed_bookings={"ABC123"},
    )

    assert "cancel_booking" in blocked(result)


async def test_reschedule_is_gated_too(scripted, call, blocked) -> None:
    """Every irreversible operation, not just the one that caused the incident."""
    result = await run_turn(
        "move it to the later flight",
        llm=scripted([call("reschedule_booking", pnr="ABC123", flight_number="SG584")], "Moved."),
    )

    assert "reschedule_booking" in blocked(result)


@pytest.mark.integration
async def test_a_failed_lookup_does_not_make_a_booking_confirmable(scripted, call) -> None:
    """
    Only a *successful* read-back earns the right to confirm.

    Marked integration: it needs the flight service to actually fail the lookup.
    A booking the system could not even find must never become confirmable.
    """
    result = await run_turn(
        "look up ABC123 then cancel it",
        llm=scripted([call("get_booking", pnr="ABC123")], "I could not find that booking."),
    )

    assert "ABC123" not in result.informed_bookings


async def test_a_blocked_turn_reports_awaiting_confirmation(scripted, call) -> None:
    """Blocking must not look like success to the caller."""
    result = await run_turn(
        "cancel ABC123",
        llm=scripted([call("cancel_booking", pnr="ABC123")], "I need to check first."),
    )

    assert result.awaiting_confirmation is True


async def test_a_read_only_tool_is_never_gated(scripted, call, executed) -> None:
    """The gate must not make the agent useless — looking things up is free."""
    result = await run_turn(
        "is AI858 on time?",
        llm=scripted([call("check_flight_status", flight_number="AI858")], "It is on time."),
    )

    assert "check_flight_status" in executed(result)
