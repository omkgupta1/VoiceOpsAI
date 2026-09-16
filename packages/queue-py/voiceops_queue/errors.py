"""
Error classification — the decision the whole retry engine turns on.

RETRYABLE: the request was fine, the service could not serve it now. Wait, retry.
PERMANENT: the request is wrong and will be wrong forever. Retrying adds load to
           a service that is answering correctly, and delays telling the customer.

Getting this wrong is expensive in both directions. Treating a permanent failure
as retryable means five pointless attempts, five backoffs, and a customer who
waits minutes to hear "that booking does not exist". Treating a transient failure
as permanent throws away work that would have succeeded on the next try.

This module deliberately classifies from the **status code** as well as from any
`retryable` flag the upstream offers. The flight service is generous enough to
send one; most real upstreams are not, and a classifier that depends on it is a
classifier that stops working the moment you point it at something else.
"""

from __future__ import annotations

from dataclasses import dataclass

# Codes this system raises itself, where the status code alone is not enough.
PERMANENT_CODES = frozenset({
    "BOOKING_NOT_FOUND", "FLIGHT_NOT_FOUND", "REFUND_NOT_FOUND",
    "ALREADY_CANCELLED", "NOT_CANCELLABLE", "NOT_RESCHEDULABLE",
    "NO_ALTERNATIVES", "INVALID_IDENTIFIER", "FLIGHT_NOT_AN_OPTION",
    "UNAUTHORIZED", "MISSING_API_KEY", "MODEL_NOT_FOUND", "BAD_REQUEST",
    "UNKNOWN_TOOL", "INVALID_ARGUMENTS", "MISSING_ARGUMENTS",
    "BINARY_MISSING", "MODEL_MISSING", "VOICE_MISSING", "FFMPEG_MISSING",
    "EMPTY_AUDIO", "EMPTY_TEXT", "CONFIRMATION_REQUIRED",
})

RETRYABLE_CODES = frozenset({
    "SERVICE_UNAVAILABLE", "RATE_LIMITED", "UPSTREAM_TIMEOUT", "INTERNAL_ERROR",
    "CONNECTION_FAILED", "MODEL_TIMEOUT", "STT_TIMEOUT", "MALFORMED_RESPONSE",
    "TRANSCRIPTION_FAILED", "SYNTHESIS_FAILED",
})


@dataclass(slots=True)
class Classification:
    retryable: bool
    reason: str


def classify(
    *,
    code: str | None = None,
    status_code: int | None = None,
    retryable_hint: bool | None = None,
) -> Classification:
    """
    Decide whether a failure is worth retrying.

    Precedence is deliberate: an explicit code we recognise beats a status code,
    and a status code beats the upstream's own hint. The hint is trusted last
    because it is the thing most likely to be absent, wrong, or attacker-supplied
    — a 404 that claims to be retryable is still a 404.
    """
    if code:
        if code in PERMANENT_CODES:
            return Classification(False, f"{code} is permanent by definition")
        if code in RETRYABLE_CODES:
            return Classification(True, f"{code} is transient by definition")

    if status_code is not None:
        if status_code == 429:
            return Classification(True, "429 rate limited")
        if status_code >= 500:
            return Classification(True, f"{status_code} server error")
        if status_code >= 400:
            # The single most valuable line here. A 4xx means we asked wrongly,
            # and asking again identically cannot change the answer.
            return Classification(False, f"{status_code} client error")
        return Classification(False, f"{status_code} is not a failure")

    if retryable_hint is not None:
        return Classification(retryable_hint, "upstream hint")

    # Unknown failures are retried once or twice rather than discarded. The
    # attempt cap bounds the cost of being wrong here.
    return Classification(True, "unclassified, treating as transient")
