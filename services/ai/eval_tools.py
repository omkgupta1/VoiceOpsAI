#!/usr/bin/env python
"""
Tool-selection eval.

Measures the one thing the voice agent's usefulness rests on: given what a
customer said, does the model call the right tool?

This exists because tuning a prompt by hand-typing one example and eyeballing the
reply is how you convince yourself of things that are not true. Prompt length,
tool count and tool descriptions all interact, and on a small model they interact
strongly — so change the prompt, run this, and compare the number.

    make eval            # one pass over every case
    make eval RUNS=3     # three samples per case, for a stabler number

Exits non-zero when the pass rate drops below THRESHOLD, so it can gate CI later.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from app.orchestrator.prompt import SYSTEM_PROMPT
from app.providers.base import Message, ProviderError
from app.providers.registry import get_llm
from app.tools.base import registry

import app.tools.flight  # noqa: F401  (registers the tools)

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

THRESHOLD = 0.70

# `None` means no tool should be called. Those cases matter as much as the
# positive ones: a model that calls something for every utterance is not
# discriminating, it is just eager.
CASES: list[tuple[str, str, str | None]] = [
    ("flight status",      "Hi, is flight AI858 delayed?",                          "check_flight_status"),
    ("flight status 2",    "What's the status of 6E455?",                           "check_flight_status"),
    ("booking lookup",     "Can you look up my booking, the reference is 7MGFXC?",  "get_booking"),
    ("booking lookup 2",   "I want to check my booking ABC123",                     "get_booking"),
    # Should look the booking up first, not cancel outright — the confirmation gate.
    ("cancel intent",      "I want to cancel my booking 7MGFXC",                    "get_booking"),
    ("explicit cancel",    "Yes, I confirm, please cancel booking 7MGFXC now",      "cancel_booking"),
    ("refund chase",       "Where is my refund for booking 7MGFXC?",                "check_refund_status"),
    ("reschedule",         "I need to move my flight, booking 7MGFXC",              "get_booking"),
    ("human request",      "Just put me through to a real person please",           "escalate_to_human"),
    ("human request 2",    "I'd like to speak to a manager",                        "escalate_to_human"),
    ("human request 3",    "Can I talk to a human agent?",                          "escalate_to_human"),
    ("chitchat",           "Hello, are you a robot?",                               None),
    ("no booking ref",     "I want to cancel my flight",                            None),
]


async def select_tool(utterance: str) -> tuple[str | None, str]:
    """Return the tool the model chose for this utterance, and any prose it gave."""
    llm = get_llm()
    response = await llm.complete(
        [Message(role="system", content=SYSTEM_PROMPT), Message(role="user", content=utterance)],
        registry.specs(),
    )
    chosen = response.tool_calls[0].name if response.tool_calls else None
    return chosen, (response.content or "").strip()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1, help="samples per case")
    args = parser.parse_args()

    llm = get_llm()
    print(f"\n  provider: {llm.name}:{llm.model}   tools: {len(registry.tools)}   "
          f"prompt: {len(SYSTEM_PROMPT)} chars   runs: {args.runs}\n")

    passed = total = 0
    failures: list[str] = []

    for label, utterance, expected in CASES:
        observed: Counter[str] = Counter()
        prose = ""
        for _ in range(args.runs):
            try:
                chosen, prose = await select_tool(utterance)
            except ProviderError as exc:
                print(f"  {RED}ERROR{RESET}  {label}: {exc.message}")
                return 2
            observed[chosen or "—"] += 1
            total += 1
            if chosen == expected:
                passed += 1

        best, count = observed.most_common(1)[0]
        ok = (best if best != "—" else None) == expected
        mark = f"{GREEN}pass{RESET}" if ok else f"{RED}fail{RESET}"
        consistency = "" if args.runs == 1 else f" {DIM}{count}/{args.runs}{RESET}"

        print(f"  {mark}  {label:<17} {DIM}expected{RESET} {expected or '(none)':<22}"
              f"{DIM}got{RESET} {best}{consistency}")
        if not ok:
            detail = f" — said: {prose[:60]}" if best == "—" and prose else ""
            failures.append(f"{label}: expected {expected or '(none)'}, got {best}{detail}")

    rate = passed / total if total else 0.0
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
