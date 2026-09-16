"""HTTP surface for the AI service."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core import persistence
from app.orchestrator.turn import run_turn
from app.providers.base import Message, ProviderError
from app.providers.registry import active, get_llm
from app.tools.base import registry

router = APIRouter(prefix="/v1", tags=["voice"])


class TurnRequest(BaseModel):
    message: str = Field(min_length=1, description="What the customer said")
    # Omit on the first turn; the response carries the id to use for the rest.
    call_id: str | None = None
    channel: str = "browser"


class TurnResponse(BaseModel):
    call_id: str
    reply: str
    intent: str | None
    escalated: bool
    tool_calls: list[dict]
    timings: dict
    providers: dict
    truncated: bool
    # True when the agent asked the customer to confirm a change rather than
    # making it. The Phase 8 dashboard surfaces this as a distinct call state.
    awaiting_confirmation: bool


def _to_messages(rows: list[dict]) -> list[Message]:
    """
    Rebuild conversation history for the model.

    Only the prose is replayed, not the tool calls: the results are already
    reflected in what the agent said, and replaying them would double the
    context a 7B model has to hold for no extra information.
    """
    return [
        Message(
            role="user" if row["speaker"] == "CUSTOMER" else "assistant",
            content=row["message"],
        )
        for row in rows
        if row["speaker"] in ("CUSTOMER", "AGENT")
    ]


@router.post("/turn", response_model=TurnResponse)
async def turn(request: TurnRequest) -> TurnResponse:
    call_id = request.call_id or await persistence.create_call(channel=request.channel)

    history = _to_messages(await persistence.load_history(call_id)) if request.call_id else []
    informed = await persistence.get_informed_bookings(call_id) if request.call_id else set()

    try:
        result = await run_turn(
            request.message, llm=get_llm(), history=history, informed_bookings=informed
        )
    except ProviderError as exc:
        # Surfaced with its retryable flag intact so the caller — and later the
        # retry engine — can tell a transient failure from a misconfiguration.
        raise HTTPException(
            status_code=503 if exc.retryable else 400,
            detail={"code": exc.code, "message": exc.message, "retryable": exc.retryable},
        ) from exc

    providers = active()
    index = await persistence.next_turn_index(call_id)

    await persistence.set_informed_bookings(call_id, result.informed_bookings)

    await persistence.record_turn(
        call_id=call_id, turn_index=index, speaker="CUSTOMER",
        message=request.message, intent=result.intent, providers=providers,
    )
    await persistence.record_turn(
        call_id=call_id, turn_index=index + 1, speaker="AGENT", message=result.reply,
        tool_calls=[i.as_dict() for i in result.tool_invocations],
        llm_ms=result.llm_ms, providers=providers,
    )

    escalation_reason = next(
        (
            i.arguments.get("reason")
            for i in result.tool_invocations
            if i.name == "escalate_to_human"
        ),
        None,
    )
    await persistence.update_call(
        call_id=call_id, intent=result.intent,
        escalated=result.escalated, escalation_reason=escalation_reason,
    )

    return TurnResponse(
        call_id=call_id,
        reply=result.reply,
        intent=result.intent,
        escalated=result.escalated,
        tool_calls=[i.as_dict() for i in result.tool_invocations],
        timings={
            "llm_ms": result.llm_ms,
            "tools_ms": result.tools_ms,
            "total_ms": result.total_ms,
            "iterations": result.iterations,
        },
        providers=providers,
        truncated=result.truncated,
        awaiting_confirmation=result.awaiting_confirmation,
    )


@router.post("/calls/{call_id}/end")
async def end_call(call_id: str) -> dict:
    await persistence.end_call(call_id=call_id)
    return {"call_id": call_id, "status": "ended"}


@router.get("/tools")
async def list_tools() -> dict:
    """What the model is offered. Useful when a tool is not being called as expected."""
    return {
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "mutating": tool.mutating,
            }
            for tool in registry.tools.values()
        ]
    }
