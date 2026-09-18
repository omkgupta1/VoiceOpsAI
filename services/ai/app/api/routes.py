"""HTTP surface for the AI service."""

from __future__ import annotations

import base64

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

import voiceops_telemetry as telemetry
from voiceops_telemetry import metrics

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

    _record_outcome(call_id, result)


def _record_outcome(call_id: str, result: TurnResult) -> None:
    """
    Stamp the finished turn onto the request span, and count it.

    The attributes go on the span FastAPI already opened for this request rather
    than on a span of their own: a child span holding nothing but attributes adds
    a row to every trace and tells you nothing the parent could not.

    Both routes reach this through `_persist`, so text turns and voice turns are
    measured the same way — a metric that only counted one of them would quietly
    under-report exactly as soon as the other got used.
    """
    escalated = result.escalated
    outcome = "escalated" if escalated else "answered" if result.reply else "failed"
    metrics.turns_total.labels(outcome).inc()

    span = telemetry.current_span()
    span.set_attribute("call.id", call_id)
    span.set_attribute("turn.outcome", outcome)
    span.set_attribute("turn.intent", result.intent or "")
    span.set_attribute("turn.escalated", escalated)
    span.set_attribute("turn.awaiting_confirmation", result.awaiting_confirmation)
    span.set_attribute("turn.iterations", result.iterations)
    span.set_attribute("turn.llm_ms", result.llm_ms)
    span.set_attribute("turn.tools_ms", result.tools_ms)
    span.set_attribute("flow.id", result.flow_id or "")
    span.set_attribute("flow.state", result.flow_state or "")
    span.set_attribute("turn.tools_called", [i.name for i in result.tool_invocations])
    if result.queued_jobs:
        # The link from a call to the background work it spawned. Those job spans
        # arrive later, under this same trace.
        span.set_attribute("turn.queued_job_ids", [j["job_id"] for j in result.queued_jobs])


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
        with telemetry.span(
            "stt.transcribe",
            attributes={
                "stt.provider": providers.get("stt"),
                "stt.bytes": len(raw),
                "stt.mime_type": audio.content_type or "audio/wav",
                "call.id": resolved_call_id,
            },
        ) as stt_span:
            transcript = await get_stt().transcribe(
                raw, mime_type=audio.content_type or "audio/wav"
            )
            stt_span.set_attribute("stt.duration_ms", transcript.duration_ms)
            # The transcript itself, because the single most common failure in
            # this pipeline is the model acting correctly on a misheard flight
            # number. Without it the trace shows a confident answer to a
            # question nobody asked.
            stt_span.set_attribute("stt.text", transcript.text or "")
        metrics.voice_stage_seconds.labels("stt", providers.get("stt", "unknown")).observe(
            transcript.duration_ms / 1000
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
        with telemetry.span(
            "tts.synthesize", attributes={"tts.characters": len(text)}
        ) as tts_span:
            rendered = await get_tts().synthesize(text)
            tts_span.set_attribute("tts.duration_ms", rendered.duration_ms)
            tts_span.set_attribute("tts.audio_ms", rendered.audio_ms)
        metrics.voice_stage_seconds.labels("tts", "piper").observe(rendered.duration_ms / 1000)
    except ProviderError:
        return None
    return {
        "mime_type": rendered.mime_type,
        "base64": base64.b64encode(rendered.data).decode(),
        "audio_ms": rendered.audio_ms,
        "synthesis_ms": rendered.duration_ms,
        "sample_rate": rendered.sample_rate,
    }
