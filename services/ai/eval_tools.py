#!/usr/bin/env python
"""
Tool-selection eval.

Measures the thing the agent's usefulness rests on: given what a customer said,
does the model call the right tool?

It also answers the question Phase 5 exists to settle. Phase 3 measured that a
7B model degrades when offered seven tools at once, and predicted that narrowing
the choice per conversational state would fix it structurally, where prompt
wording could not. This compares the two directly:

    make eval               # flow-scoped, per state
    make eval RUNS=3        # three samples per case
    make eval MODE=compare  # flow-scoped vs every tool at once

Exits non-zero below THRESHOLD, so it can gate CI later.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from app.flows.loader import get_flow
from app.orchestrator.prompt import SYSTEM_PROMPT
from app.providers.base import Message, ProviderError
from app.providers.registry import get_llm
from app.tools.base import registry

import app.tools.flight  # noqa: F401  (registers the tools)

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

THRESHOLD = 0.70

# (state, label, utterance, expected tool or None)
#
# Expectations are per state, because the right answer genuinely differs. From
# `identify` the correct response to "cancel my booking" is to look it up — the
# agent does not yet know which booking, and cancelling is irreversible. From
# `servicing`, with the booking already read back, cancelling is correct.
CASES: list[tuple[str, str, str, str | None]] = [
    ("identify",  "flight status",    "Hi, is flight AI858 delayed?",                         "check_flight_status"),
    ("identify",  "flight status 2",  "What's the status of 6E455?",                          "check_flight_status"),
    ("identify",  "booking lookup",   "Can you look up my booking, the reference is 7MGFXC?", "get_booking"),
    ("identify",  "cancel intent",    "I want to cancel my booking 7MGFXC",                   "get_booking"),
    ("identify",  "refund chase",     "Where is my refund for booking 7MGFXC?",               "get_booking"),
    ("identify",  "reschedule",       "I need to move my flight, booking 7MGFXC",             "get_booking"),
    ("identify",  "human request",    "Just put me through to a real person please",          "escalate_to_human"),
    ("identify",  "human request 2",  "I'd like to speak to a manager",                       "escalate_to_human"),
    ("identify",  "human request 3",  "Can I talk to a human agent?",                         "escalate_to_human"),
    # Live external data. These share the state with the schedule lookup, so they
    # also test whether the model can tell "is it delayed" from "where is it".
    ("identify",  "weather",          "What's the weather like in Delhi right now?",          "check_airport_weather"),
    ("identify",  "weather delay",    "Is the weather at BOM going to delay flights?",        "check_airport_weather"),
    ("identify",  "where is plane",   "Where is flight AI302 right now?",                     "check_aircraft_position"),
    ("identify",  "has it taken off", "Has flight AI302 taken off yet?",                      "check_aircraft_position"),
    ("identify",  "chitchat",         "Hello, are you a robot?",                              None),
    ("identify",  "no reference",     "I want to cancel my flight",                           None),

    ("servicing", "confirmed cancel", "Yes, that's right, please cancel it",                  "cancel_booking"),
    ("servicing", "wants options",    "Can you show me other flights I could move to?",       "get_reschedule_options"),
    ("servicing", "refund status",    "Has my refund been processed yet?",                    "check_refund_status"),
    ("servicing", "escalate",         "This isn't working, get me a person",                  "escalate_to_human"),

    ("choosing_flight", "picks one",  "Yes, the first one works for me, book it",             "reschedule_booking"),
]


async def choose(utterance: str, tools: list[str] | None) -> tuple[str | None, str]:
    """What the model picks, given this state's tools."""
    response = await get_llm().complete(
        [Message(role="system", content=SYSTEM_PROMPT), Message(role="user", content=utterance)],
        registry.specs(tools),
    )
    return (
        response.tool_calls[0].name if response.tool_calls else None,
        (response.content or "").strip(),
    )


async def run(scoped: bool, runs: int, verbose: bool = True) -> tuple[int, int, list[str]]:
    flow = get_flow()
    passed = total = 0
    failures: list[str] = []
    seen_state = None

    for state_name, label, utterance, expected in CASES:
        state = flow.state(state_name)
        tools = state.tools if scoped else None

        if verbose and state_name != seen_state:
            count = len(state.tools) if scoped else len(registry.tools)
            print(f"\n  {DIM}state{RESET} {state_name}  {DIM}({count} tools offered){RESET}")
            seen_state = state_name

        observed: Counter[str] = Counter()
        prose = ""
        for _ in range(runs):
            try:
                chosen, prose = await choose(utterance, tools)
            except ProviderError as exc:
                print(f"  {RED}ERROR{RESET} {exc.message}")
                raise SystemExit(2) from exc
            observed[chosen or "—"] += 1
            total += 1
            passed += chosen == expected

        best, count = observed.most_common(1)[0]
        ok = (best if best != "—" else None) == expected

        if verbose:
            mark = f"{GREEN}pass{RESET}" if ok else f"{RED}fail{RESET}"
            consistency = "" if runs == 1 else f" {DIM}{count}/{runs}{RESET}"
            print(f"    {mark}  {label:<17} {DIM}want{RESET} {expected or '(none)':<23}"
                  f"{DIM}got{RESET} {best}{consistency}")
        if not ok:
            detail = f" — said: {prose[:55]}" if best == "—" and prose else ""
            failures.append(f"{state_name}/{label}: want {expected or '(none)'}, got {best}{detail}")

    return passed, total, failures


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--mode", choices=["scoped", "compare"], default="scoped")
    args = parser.parse_args()

    llm = get_llm()
    print(f"\n  provider {llm.name}:{llm.model}   prompt {len(SYSTEM_PROMPT)} chars   "
          f"runs {args.runs}")

    if args.mode == "compare":
        print(f"\n{DIM}  Does narrowing the tool set per state actually help?{RESET}")
        unscoped = await run(scoped=False, runs=args.runs, verbose=False)
        scoped = await run(scoped=True, runs=args.runs, verbose=False)
        for name, (passed, total, _) in (("all tools at once", unscoped), ("flow-scoped", scoped)):
            rate = passed / total
            colour = GREEN if rate >= THRESHOLD else YELLOW
            print(f"    {colour}{passed:>2}/{total} ({rate:>4.0%}){RESET}  {name}")
        delta = scoped[0] / scoped[1] - unscoped[0] / unscoped[1]
        print(f"\n    {'+' if delta >= 0 else ''}{delta:.0%} from scoping\n")
        return 0 if scoped[0] / scoped[1] >= THRESHOLD else 1

    passed, total, failures = await run(scoped=True, runs=args.runs)
    rate = passed / total
    colour = GREEN if rate >= THRESHOLD else (YELLOW if rate >= 0.5 else RED)
    print(f"\n  {colour}{passed}/{total} correct ({rate:.0%}){RESET}   threshold {THRESHOLD:.0%}\n")

    if failures:
        print(f"  {DIM}failing cases:{RESET}")
        for failure in failures:
            print(f"    - {failure}")
        print()
    return 0 if rate >= THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
