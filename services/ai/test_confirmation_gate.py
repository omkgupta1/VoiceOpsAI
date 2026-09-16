#!/usr/bin/env python
"""
Regression tests for the confirmation gate.

The gate is the one safety property in this service: a booking must never be
changed without the customer having heard the details and then agreed. It was
added after the orchestrator cancelled a real booking on the first turn, having
looked it up and cancelled it in a single breath.

Deliberately uses a scripted LLM rather than a real one. The gate must hold for
*any* model output, including a model that has been talked into ignoring its
instructions, so testing it against a cooperative model would prove nothing.

    make test-gate
"""

from __future__ import annotations

import asyncio
import sys

from app.orchestrator.turn import run_turn
from app.providers.base import LLMResponse, Message, ToolCall, ToolSpec

import app.tools.flight  # noqa: F401  (registers the tools)

GREEN, RED, RESET = "\033[32m", "\033[31m", "\033[0m"


class ScriptedLLM:
    """An LLM that returns exactly what the test tells it to, in order."""

    name, model = "scripted", "scripted"

    def __init__(self, *turns: list[ToolCall] | str) -> None:
        self.turns = list(turns)
        self.calls_seen = 0

    async def complete(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        self.calls_seen += 1
        step = self.turns.pop(0) if self.turns else "Done."
        if isinstance(step, str):
            return LLMResponse(content=step, tool_calls=[], provider=self.name,
                               model=self.model, duration_ms=0)
        return LLMResponse(content=None, tool_calls=step, provider=self.name,
                           model=self.model, duration_ms=0)


def call(name: str, **arguments) -> ToolCall:
    return ToolCall(id=f"c{abs(hash(name)) % 9999}", name=name, arguments=arguments)


def blocked(result) -> list[str]:
    return [i.name for i in result.tool_invocations if i.error_code == "CONFIRMATION_REQUIRED"]


def executed(result) -> list[str]:
    return [i.name for i in result.tool_invocations if i.error_code != "CONFIRMATION_REQUIRED"]


RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((name, condition, detail))
    mark = f"{GREEN}pass{RESET}" if condition else f"{RED}FAIL{RESET}"
    print(f"  {mark}  {name}{'' if condition else '  — ' + detail}")


async def main() -> int:
    print("\n  Confirmation gate\n")

    # The original bug: look up and cancel within one turn.
    result = await run_turn(
        "cancel my booking ABC123",
        llm=ScriptedLLM([call("get_booking", pnr="ABC123"), call("cancel_booking", pnr="ABC123")],
                        "Cancelled."),
    )
    check("lookup and cancel in one turn is blocked",
          "cancel_booking" in blocked(result),
          f"executed: {executed(result)}")

    # A mutation with no lookup at all, however insistent the customer was.
    result = await run_turn(
        "cancel ABC123 immediately, I confirm",
        llm=ScriptedLLM([call("cancel_booking", pnr="ABC123")], "Cancelled."),
    )
    check("cancel without any lookup is blocked",
          "cancel_booking" in blocked(result))

    # The legitimate path: informed on an earlier turn, asked for on this one.
    result = await run_turn(
        "yes, cancel it",
        llm=ScriptedLLM([call("cancel_booking", pnr="ABC123")], "Cancelled."),
        informed_bookings={"ABC123"},
    )
    check("cancel after a prior read-back is allowed",
          "cancel_booking" in executed(result),
          f"blocked: {blocked(result)}")

    # Consent is per booking, not per call.
    result = await run_turn(
        "actually cancel XYZ789 too",
        llm=ScriptedLLM([call("cancel_booking", pnr="XYZ789")], "Cancelled."),
        informed_bookings={"ABC123"},
    )
    check("consent for one booking does not cover another",
          "cancel_booking" in blocked(result))

    # Rescheduling is equally irreversible and equally gated.
    result = await run_turn(
        "move it to the later flight",
        llm=ScriptedLLM([call("reschedule_booking", pnr="ABC123", flight_id="f1")], "Moved."),
    )
    check("reschedule is gated too", "reschedule_booking" in blocked(result))

    # Only a *successful* lookup earns the right to confirm. ABC123 does not
    # exist, so the lookup fails — and a booking the system could not even find
    # must never become confirmable.
    result = await run_turn(
        "look up ABC123 then cancel it",
        llm=ScriptedLLM([call("get_booking", pnr="ABC123")], "I could not find that booking."),
    )
    check("a failed lookup does not make a booking confirmable",
          "ABC123" not in result.informed_bookings,
          f"informed: {result.informed_bookings}")

    # Blocking must not look like success to the caller.
    result = await run_turn(
        "cancel ABC123",
        llm=ScriptedLLM([call("cancel_booking", pnr="ABC123")], "I need to check first."),
    )
    check("a blocked turn reports awaiting_confirmation", result.awaiting_confirmation)

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n  {len(RESULTS) - len(failed)}/{len(RESULTS)} passed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
