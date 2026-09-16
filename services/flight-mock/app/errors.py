"""
The error taxonomy.

Every failure this service produces is explicitly one of two things:

  PERMANENT — the request is wrong and will be wrong forever. Retrying cannot
              help; it only adds load to a service that is answering correctly.
              Example: no booking matches that PNR.

  RETRYABLE — the request is fine but the service could not serve it right now.
              Retrying after a backoff is the correct response.
              Example: the upstream returned 503.

This distinction is the reason the project exists, so it is modelled here at the
boundary rather than inferred later from a status code.

Note for the retry engine (Phase 6): the `retryable` flag in the response body is
a convenience this service chooses to offer. A client must still be able to
classify from the status code alone, because real upstreams rarely tell you.
"""

from __future__ import annotations

from fastapi import HTTPException


class ServiceError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, retryable: bool) -> None:
        super().__init__(
            status_code=status_code,
            detail={"code": code, "message": message, "retryable": retryable},
        )
        self.code = code
        self.retryable = retryable


# ---------- Permanent: do not retry ----------

def booking_not_found(pnr: str) -> ServiceError:
    return ServiceError(404, "BOOKING_NOT_FOUND", f"No booking matches PNR {pnr}", False)


def flight_not_found(flight_number: str) -> ServiceError:
    return ServiceError(404, "FLIGHT_NOT_FOUND", f"No flight matches {flight_number}", False)


def refund_not_found(reference: str) -> ServiceError:
    return ServiceError(404, "REFUND_NOT_FOUND", f"No refund matches {reference}", False)


def already_cancelled(pnr: str) -> ServiceError:
    return ServiceError(409, "ALREADY_CANCELLED", f"Booking {pnr} is already cancelled", False)


def not_cancellable(reason: str) -> ServiceError:
    return ServiceError(422, "NOT_CANCELLABLE", reason, False)


def not_reschedulable(reason: str) -> ServiceError:
    return ServiceError(422, "NOT_RESCHEDULABLE", reason, False)


def invalid_identifier(field: str, value: str) -> ServiceError:
    return ServiceError(
        422, "INVALID_IDENTIFIER",
        f"{field} must be a flight id from reschedule-options, not '{value}'",
        False,
    )


def no_alternatives(pnr: str) -> ServiceError:
    return ServiceError(422, "NO_ALTERNATIVES", f"No alternative flights for booking {pnr}", False)


# ---------- Retryable: back off and try again ----------

def service_unavailable() -> ServiceError:
    return ServiceError(503, "SERVICE_UNAVAILABLE", "Flight service is temporarily unavailable", True)


def rate_limited() -> ServiceError:
    return ServiceError(429, "RATE_LIMITED", "Too many requests to the flight service", True)


def upstream_timeout() -> ServiceError:
    return ServiceError(504, "UPSTREAM_TIMEOUT", "Flight service did not respond in time", True)


def internal_error() -> ServiceError:
    return ServiceError(500, "INTERNAL_ERROR", "Unexpected failure in the flight service", True)
