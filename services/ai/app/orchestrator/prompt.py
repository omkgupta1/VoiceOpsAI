"""
The system prompt.

Kept deliberately short, and that is a measured decision rather than a stylistic
one. On qwen2.5:7b-instruct with seven tools registered, prompt length trades
directly against tool-calling reliability: an earlier 1,400-character version of
this prompt caused the model to narrate ("let me check that for you") instead of
calling any tool at all, while the same prompt worked fine when only one tool was
offered. An ablation over four variants isolated it — every additional
instruction cost tool accuracy.

Two rules follow from that, and both shape the code around this file:

  1. Guidance about a specific tool belongs in that tool's `description`, not
     here. The model reads a description in the context of deciding whether to
     call that tool, which is exactly when the guidance is relevant. Moving the
     escalation guidance into `escalate_to_human` fixed a case this prompt could
     not.

  2. The real fix is structural, not textual. Phase 5's flow engine narrows the
     tools offered at each conversational state, so the model chooses between one
     or two rather than seven. Prompt tuning cannot buy what that buys.

  3. Prompt lines interact, so measure the combination rather than the line. The
     closing "call the tool in the same reply" sentence *hurt* when the escalation
     tool still had its original description, and *helped* once that description
     was rewritten — 69% to 92% on the same suite. A line judged in isolation is
     judged wrong.

Runs vary: the same prompt scored 77% and 69% on consecutive single passes. Use
`make eval RUNS=3` before believing a difference.

`scripts/eval_tools.py` measures this. Change this file and run it — do not
guess, and do not trust a single hand-typed example.
"""

from __future__ import annotations

SYSTEM_PROMPT = """You are a voice support agent for an airline, speaking with a \
customer on a phone call.
Your reply is read aloud, so write plain spoken English. No markdown, no bullet \
points, no emoji. Keep it to one or two short sentences.
Use your tools to get facts. Never state a flight time, fare, refund amount or \
booking detail that did not come from a tool result.
Cancelling and rescheduling cannot be undone: look the booking up, read the \
details back, and get a clear yes before you call those tools.
If you tell the customer you are doing something, call the tool that does it in \
the same reply."""
