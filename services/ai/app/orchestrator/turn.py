"""
The turn orchestrator.

One customer utterance in, one agent reply out, with any number of tool calls in
between. This is the loop described in overview.md §3.1.

Two things it guarantees:

  * The loop is bounded. A model that keeps asking for tools stops after
    `max_tool_iterations` and is made to answer, rather than spinning until some
    unrelated timeout fires.
  * A tool failure never aborts the turn. The failure is handed back to the model
    as a tool result so it can explain the problem to the customer — which is the
    reason for putting a language model in front of an API in the first place.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import voiceops_telemetry as telemetry
from voiceops_telemetry import metrics

from app.config import settings
from app.core.queue import enqueue_retry
from app.flows.loader import get_flow
from app.orchestrator.prompt import SYSTEM_PROMPT
from app.providers.base import LLMProvider, Message, ProviderError
from app.tools.base import ToolResult, registry

# Which tool defines the intent of a turn. Derived from what the model actually
# did rather than asked for separately: one fewer model call, and it cannot
# disagree with the action that was taken.
_TOOL_INTENTS = {
    "check_flight_status": "CHECK_FLIGHT_STATUS",
    "get_booking": "GET_BOOKING_DETAILS",
    "cancel_booking": "CANCEL_BOOKING",
    "get_reschedule_options": "RESCHEDULE_FLIGHT",
    "reschedule_booking": "RESCHEDULE_FLIGHT",
    "check_refund_status": "CHECK_REFUND_STATUS",
    "escalate_to_human": "SPEAK_TO_HUMAN",
}


@dataclass(slots=True)
class ToolInvocation:
    name: str
    arguments: dict[str, Any]
    ok: bool
    duration_ms: int
    error_code: str | None = None
    error_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


# Tools whose success means the customer has now heard a booking's details read
# back to them. Only after that can a change to it be confirmed.
_SURFACING_TOOLS = {"get_booking", "get_reschedule_options"}


@dataclass(slots=True)
class TurnResult:
    reply: str
    intent: str | None
    escalated: bool
    tool_invocations: list[ToolInvocation] = field(default_factory=list)
    llm_ms: int = 0
    tools_ms: int = 0
    total_ms: int = 0
    iterations: int = 0
    provider: str = ""
    model: str = ""
    # Set when the loop hit its cap, so the dashboard can distinguish "answered"
    # from "gave up asking for tools".
    truncated: bool = False
    # Booking references whose details have been read back to the customer at
    # some point in this call. Persisted on the call and handed back next turn.
    informed_bookings: set[str] = field(default_factory=set)
    # True when this turn was spent asking for confirmation rather than acting.
    awaiting_confirmation: bool = False
    # Operations that failed transiently and were handed to the queue to finish.
    queued_jobs: list[dict[str, Any]] = field(default_factory=list)
    # Where the conversation sits in its flow, carried to the next turn.
    flow_id: str = ""
    flow_state: str = ""


# Said to the customer when the model produced no usable text. Better a plain
# apology than an empty response played as silence down the line.
_FALLBACK_REPLY = (
    "Sorry, I ran into a problem handling that. Let me pass you to a colleague."
)


# Returned to the model in place of running a blocked mutating tool. Phrased as
# an instruction because the model reads it as the result of its own call and
# needs to know what to do next.
_NEEDS_READBACK = (
    "CONFIRMATION_REQUIRED: nothing has been changed. You have not yet read this "
    "booking's details back to the customer. Look the booking up, tell them the "
    "flight and the date, and ask them to confirm. Only call this tool after they "
    "have answered."
)

_SAME_TURN = (
    "CONFIRMATION_REQUIRED: nothing has been changed. You have just looked this "
    "booking up, so the customer has not heard the details yet. Read them back now "
    "and ask them to confirm. Call this tool only after they answer."
)


async def run_turn(
    user_message: str,
    *,
    llm: LLMProvider,
    history: list[Message] | None = None,
    informed_bookings: set[str] | None = None,
    flow_id: str | None = None,
    flow_state: str | None = None,
    call_id: str | None = None,
) -> TurnResult:
    started = time.perf_counter()

    flow = get_flow(flow_id)
    current_state = flow_state if flow_state in flow.states else flow.initial

    # A state's `purpose` is documentation for whoever reads the YAML. It is
    # deliberately NOT appended to the system prompt: adding that one line cost
    # 28% of tool-selection accuracy with all tools offered, and 12% when
    # scoped, on the same eval suite. The narrowed tool list already tells the
    # model what it may do here, and it tells it far more cheaply than prose.
    messages: list[Message] = [Message(role="system", content=SYSTEM_PROMPT)]
    messages.extend(history or [])
    messages.append(Message(role="user", content=user_message))
    invocations: list[ToolInvocation] = []
    llm_ms = tools_ms = iterations = 0
    awaiting_confirmation = False
    queued_jobs: list[dict[str, Any]] = []
    informed: set[str] = set(informed_bookings or ())
    # Tools that actually executed this turn, as opposed to ones that were blocked.
    executed_this_turn = 0
    provider = model = ""
    truncated = False
    reply: str | None = None

    for iteration in range(1, max(settings.max_tool_iterations, 1) + 1):
        iterations = iteration
        # On the final pass, withhold the tools entirely. Asking a model to
        # "stop calling tools" is unreliable; removing them is not.
        offer_tools = iteration < settings.max_tool_iterations

        # Recomputed each pass: a tool call can move the conversation to a new
        # state, and the next choice must be made from that state's tools.
        specs = registry.specs(flow.state(current_state).tools) if offer_tools else None

        with telemetry.span(
            "llm.complete",
            kind=telemetry.SpanKind.CLIENT,
            attributes={
                "llm.iteration": iteration,
                "llm.tools_offered": len(specs) if specs else 0,
                "flow.state": current_state,
            },
        ) as llm_span:
            response = await llm.complete(messages, specs)
            llm_span.set_attribute("llm.provider", response.provider)
            llm_span.set_attribute("llm.model", response.model)
            llm_span.set_attribute("llm.duration_ms", response.duration_ms)
            llm_span.set_attribute("llm.wants_tools", response.wants_tools)
            if response.wants_tools:
                llm_span.set_attribute(
                    "llm.tools_requested", [c.name for c in response.tool_calls]
                )

        metrics.voice_stage_seconds.labels("llm", response.provider).observe(
            response.duration_ms / 1000
        )
        llm_ms += response.duration_ms
        provider, model = response.provider, response.model

        if not response.wants_tools:
            reply = (response.content or "").strip() or None
            break

        messages.append(
            Message(role="assistant", content=response.content, tool_calls=response.tool_calls)
        )

        for call in response.tool_calls:
            tool = registry.get(call.name)

            # ---- The confirmation gate ----
            #
            # A state-changing tool runs only when both hold:
            #
            #   1. The customer has already had this booking's details read back
            #      to them, on an earlier turn of this call.
            #   2. No other tool has run yet this turn, so the call is a direct
            #      response to what the customer just said rather than the tail
            #      of a lookup the model performed on its own initiative.
            #
            # Together those mean the customer heard what would happen and then
            # asked for it. Either alone is not enough: (1) without (2) lets the
            # model look up and cancel in one breath, and (2) without (1) lets it
            # cancel a booking the customer has never had described to them.
            #
            # Enforced here, not in the prompt, because an irreversible action
            # cannot rest on a model choosing to comply. The first version of
            # this code trusted the prompt and cancelled a real booking without
            # ever asking.
            if tool is not None and tool.mutating:
                pnr = str(call.arguments.get("pnr", "")).upper()
                blocked_reason = None
                if executed_this_turn > 0:
                    blocked_reason = _SAME_TURN
                elif pnr not in informed:
                    blocked_reason = _NEEDS_READBACK

                if blocked_reason is not None:
                    awaiting_confirmation = True
                    # Counted so the gate is observable. A structural rule that
                    # nobody can see firing is one nobody notices has stopped.
                    metrics.confirmation_gate_total.labels(
                        "same_turn" if blocked_reason is _SAME_TURN else "needs_readback"
                    ).inc()
                    metrics.tool_calls_total.labels(call.name, "blocked").inc()
                    invocations.append(
                        ToolInvocation(
                            name=call.name,
                            arguments=call.arguments,
                            ok=False,
                            duration_ms=0,
                            error_code="CONFIRMATION_REQUIRED",
                            error_message="Blocked pending customer confirmation",
                        )
                    )
                    messages.append(
                        Message(
                            role="tool",
                            content=blocked_reason,
                            tool_call_id=call.id,
                            name=call.name,
                        )
                    )
                    continue

            with telemetry.span(
                f"tool {call.name}",
                attributes={
                    "tool.name": call.name,
                    "tool.mutating": bool(tool and tool.mutating),
                    "tool.arguments": json.dumps(call.arguments, default=str),
                },
            ) as tool_span:
                result: ToolResult = await registry.run(call.name, call.arguments)
                tool_span.set_attribute("tool.ok", result.ok)
                tool_span.set_attribute("tool.duration_ms", result.duration_ms)
                if not result.ok:
                    # A tool failure travels as a return value, never an
                    # exception, so nothing here raises for the span to catch.
                    telemetry.fail(
                        tool_span,
                        result.error_message or "tool failed",
                        code=result.error_code,
                    )
                    tool_span.set_attribute("error.retryable", bool(result.retryable))

            metrics.tool_calls_total.labels(call.name, "ok" if result.ok else "error").inc()
            metrics.tool_duration_seconds.labels(call.name).observe(result.duration_ms / 1000)
            if not result.ok:
                metrics.upstream_errors_total.labels(
                    "flight-api",
                    "RETRYABLE" if result.retryable else "PERMANENT",
                    result.error_code or "UNKNOWN",
                ).inc()

            executed_this_turn += 1

            # A confirmed change that failed for a transient reason is handed to
            # the queue rather than lost. The customer already said yes; the
            # flight service being briefly unreachable is not their problem, and
            # asking them to call back and repeat an instruction they have
            # already given is the failure the queue exists to prevent.
            if tool is not None and tool.mutating and not result.ok and result.retryable:
                queued = await enqueue_retry(
                    tool=call.name, arguments=call.arguments, call_id=call_id,
                    error=result.error_message or result.error_code or "unknown",
                )
                if queued is not None:
                    queued_jobs.append({"job_id": queued.id, "tool": call.name})
                    # Tell the model what actually happened, so it can tell the
                    # customer the truth: the request is in hand, not lost.
                    messages.append(
                        Message(
                            role="tool",
                            content=(
                                "QUEUED_FOR_RETRY: the service is temporarily unavailable, "
                                "so this request has been queued and will be completed "
                                "automatically. Reassure the customer that it is in hand "
                                "and that they do not need to call back. Do not call this "
                                "tool again."
                            ),
                            tool_call_id=call.id,
                            name=call.name,
                        )
                    )
                    invocations.append(
                        ToolInvocation(
                            name=call.name, arguments=call.arguments, ok=False,
                            duration_ms=result.duration_ms,
                            error_code="QUEUED_FOR_RETRY",
                            error_message=result.error_message,
                        )
                    )
                    # Consent was given and is now owned by the job.
                    informed.discard(str(call.arguments.get("pnr", "")).upper())
                    continue

            # A successful lookup is what makes a later change confirmable.
            if result.ok and call.name in _SURFACING_TOOLS:
                if pnr_value := str(call.arguments.get("pnr", "")).upper():
                    informed.add(pnr_value)

            # Consent is spent on use. A booking that was just changed must be
            # looked up and confirmed again before it can be changed a second time.
            if tool is not None and tool.mutating:
                informed.discard(str(call.arguments.get("pnr", "")).upper())
                awaiting_confirmation = False

            # Advance the flow on what actually happened, not on what was asked
            # for. A failed lookup must not open up tools that assume it worked.
            if (moved := flow.next_state(current_state, call.name, result.ok)) is not None:
                current_state = moved

            tools_ms += result.duration_ms
            invocations.append(
                ToolInvocation(
                    name=call.name,
                    arguments=call.arguments,
                    ok=result.ok,
                    duration_ms=result.duration_ms,
                    error_code=result.error_code,
                    error_message=result.error_message,
                )
            )
            messages.append(
                Message(
                    role="tool",
                    content=result.to_model_content(),
                    tool_call_id=call.id,
                    name=call.name,
                )
            )
    else:
        truncated = True

    if reply is None:
        # Either the loop ran out, or the model returned tool calls with no prose.
        # Ask once more with no tools available; if that also fails, apologise
        # rather than play silence.
        try:
            final = await llm.complete(messages, None)
            llm_ms += final.duration_ms
            reply = (final.content or "").strip() or _FALLBACK_REPLY
        except ProviderError:
            reply = _FALLBACK_REPLY

    escalated = any(i.name == "escalate_to_human" and i.ok for i in invocations)

    intent = None
    for invocation in reversed(invocations):
        if invocation.name in _TOOL_INTENTS:
            intent = _TOOL_INTENTS[invocation.name]
            break

    return TurnResult(
        reply=reply,
        intent=intent,
        escalated=escalated,
        tool_invocations=invocations,
        llm_ms=llm_ms,
        tools_ms=tools_ms,
        total_ms=int((time.perf_counter() - started) * 1000),
        iterations=iterations,
        provider=provider,
        model=model,
        truncated=truncated,
        informed_bookings=informed,
        awaiting_confirmation=awaiting_confirmation,
        queued_jobs=queued_jobs,
        flow_id=flow.id,
        flow_state=current_state,
    )
