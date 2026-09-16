"""HTTP surface for the AI service."""

from __future__ import annotations

import base64

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.core import persistence
from app.orchestrator.turn import TurnResult, run_turn
from app.providers.base import Message, ProviderError
from app.providers.registry import active, get_llm, get_stt, get_tts
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
    flow: dict
    # Operations handed to the queue because they failed transiently mid-call.
    queued_jobs: list[dict]


# Said back when the microphone produced no words. Short on purpose: it is
# spoken aloud, and the customer is being asked to simply repeat themselves.
_NOTHING_HEARD = "Sorry, I didn't catch that. Could you say it again?"


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


async def _persist(
    *,
    call_id: str,
    user_message: str,
    result: TurnResult,
    providers: dict,
    stt_ms: int | None = None,
    tts_ms: int | None = None,
    confidence: float | None = None,
) -> None:
    """Record both halves of a completed exchange and roll it up onto the call."""
    await persistence.set_informed_bookings(call_id, result.informed_bookings)
    await persistence.set_flow_position(call_id, result.flow_id, result.flow_state)
    index = await persistence.next_turn_index(call_id)

    await persistence.record_turn(
        call_id=call_id, turn_index=index, speaker="CUSTOMER", message=user_message,
        intent=result.intent, confidence=confidence, stt_ms=stt_ms, providers=providers,
    )
    await persistence.record_turn(
        call_id=call_id, turn_index=index + 1, speaker="AGENT", message=result.reply,
        tool_calls=[i.as_dict() for i in result.tool_invocations],
        llm_ms=result.llm_ms, tts_ms=tts_ms, providers=providers,
    )

    escalation_reason = next(
        (i.arguments.get("reason") for i in result.tool_invocations
         if i.name == "escalate_to_human"),
        None,
    )
    await persistence.update_call(
        call_id=call_id, intent=result.intent,
        escalated=result.escalated, escalation_reason=escalation_reason,
    )


@router.post("/turn", response_model=TurnResponse)
async def turn(request: TurnRequest) -> TurnResponse:
    call_id = request.call_id or await persistence.create_call(channel=request.channel)

    history = _to_messages(await persistence.load_history(call_id)) if request.call_id else []
    informed = await persistence.get_informed_bookings(call_id) if request.call_id else set()
    flow_id, flow_state = (
        await persistence.get_flow_position(call_id) if request.call_id else (None, None)
    )

    try:
        result = await run_turn(
            request.message, llm=get_llm(), history=history, informed_bookings=informed,
            flow_id=flow_id, flow_state=flow_state, call_id=call_id,
        )
    except ProviderError as exc:
        # Surfaced with its retryable flag intact so the caller — and later the
        # retry engine — can tell a transient failure from a misconfiguration.
        raise HTTPException(
            status_code=503 if exc.retryable else 400,
            detail={"code": exc.code, "message": exc.message, "retryable": exc.retryable},
        ) from exc

    providers = active()
    await _persist(call_id=call_id, user_message=request.message, result=result,
                   providers=providers)

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
        flow={"id": result.flow_id, "state": result.flow_state},
        queued_jobs=result.queued_jobs,
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


class AudioTurnResponse(BaseModel):
    call_id: str
    # What we heard. Surfaced separately from the reply because a wrong answer is
    # usually a misheard question, and without this you cannot tell the two apart.
    transcript: str
    reply: str
    intent: str | None
    escalated: bool
    tool_calls: list[dict]
    timings: dict
    providers: dict
    truncated: bool
    awaiting_confirmation: bool
    flow: dict
    queued_jobs: list[dict]
    audio: dict | None


@router.post("/turn/audio", response_model=AudioTurnResponse)
async def audio_turn(
    audio: UploadFile = File(..., description="Recorded speech (WebM/Opus, WAV, MP4…)"),
    call_id: str | None = Form(None),
    channel: str = Form("browser"),
) -> AudioTurnResponse:
    """
    One spoken exchange: speech in, speech out.

    Audio comes back as base64 in the JSON rather than as a binary body, so a
    single round trip carries the reply, what we heard, and what the agent did.
    Phase 12 will move the audio to S3 and return a URL; at conversational
    lengths (tens of kilobytes) inlining it is not worth a second request.
    """
    raw = await audio.read()
    resolved_call_id = call_id or await persistence.create_call(channel=channel)
    providers = active()

    # ---- Speech to text ----
    try:
        transcript = await get_stt().transcribe(
            raw, mime_type=audio.content_type or "audio/wav"
        )
    except ProviderError as exc:
        raise HTTPException(
            status_code=503 if exc.retryable else 400,
            detail={"code": exc.code, "message": exc.message, "retryable": exc.retryable},
        ) from exc

    # Silence, or a mis-click. Answered directly: running an empty string through
    # the model would produce a confident reply to nothing at all.
    if not transcript.text:
        return AudioTurnResponse(
            call_id=resolved_call_id, transcript="", reply=_NOTHING_HEARD,
            intent=None, escalated=False, tool_calls=[],
            timings={"stt_ms": transcript.duration_ms, "llm_ms": 0, "tts_ms": 0,
                     "tools_ms": 0, "total_ms": transcript.duration_ms},
            providers=providers, truncated=False, awaiting_confirmation=False,
            flow={"id": "", "state": ""}, queued_jobs=[],
            audio=await _speak(_NOTHING_HEARD),
        )

    # ---- Reasoning and tools ----
    history = _to_messages(await persistence.load_history(resolved_call_id)) if call_id else []
    informed = await persistence.get_informed_bookings(resolved_call_id) if call_id else set()
    flow_id, flow_state = (
        await persistence.get_flow_position(resolved_call_id) if call_id else (None, None)
    )

    try:
        result = await run_turn(
            transcript.text, llm=get_llm(), history=history, informed_bookings=informed,
            flow_id=flow_id, flow_state=flow_state, call_id=resolved_call_id,
        )
    except ProviderError as exc:
        raise HTTPException(
            status_code=503 if exc.retryable else 400,
            detail={"code": exc.code, "message": exc.message, "retryable": exc.retryable},
        ) from exc

    # ---- Text to speech ----
    # A synthesis failure must not lose the answer: the text is already correct
    # and the caller can still show it, so this degrades to a silent reply.
    spoken = await _speak(result.reply)

    await _persist(
        call_id=resolved_call_id, user_message=transcript.text, result=result,
        providers=providers, stt_ms=transcript.duration_ms,
        tts_ms=spoken["synthesis_ms"] if spoken else None,
        confidence=transcript.confidence,
    )

    return AudioTurnResponse(
        call_id=resolved_call_id,
        transcript=transcript.text,
        reply=result.reply,
        intent=result.intent,
        escalated=result.escalated,
        tool_calls=[i.as_dict() for i in result.tool_invocations],
        timings={
            "stt_ms": transcript.duration_ms,
            "llm_ms": result.llm_ms,
            "tts_ms": spoken["synthesis_ms"] if spoken else 0,
            "tools_ms": result.tools_ms,
            "total_ms": transcript.duration_ms + result.total_ms
                        + (spoken["synthesis_ms"] if spoken else 0),
        },
        providers=providers,
        truncated=result.truncated,
        awaiting_confirmation=result.awaiting_confirmation,
        flow={"id": result.flow_id, "state": result.flow_state},
        queued_jobs=result.queued_jobs,
        audio=spoken,
    )


async def _speak(text: str) -> dict | None:
    """Synthesise a reply, returning None rather than raising if TTS is unavailable."""
    try:
        rendered = await get_tts().synthesize(text)
    except ProviderError:
        return None
    return {
        "mime_type": rendered.mime_type,
        "base64": base64.b64encode(rendered.data).decode(),
        "audio_ms": rendered.audio_ms,
        "synthesis_ms": rendered.duration_ms,
        "sample_rate": rendered.sample_rate,
    }
