"""Job model and the two enumerations that govern its life."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Priority(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# Highest first. This is the order the consumer polls in, so it is the order
# that actually determines which job a free worker picks up.
PRIORITY_ORDER: tuple[Priority, ...] = (Priority.HIGH, Priority.MEDIUM, Priority.LOW)


class Status(StrEnum):
    SCHEDULED = "SCHEDULED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RETRY_WAIT = "RETRY_WAIT"
    DEAD_LETTER = "DEAD_LETTER"
    CANCELLED = "CANCELLED"


@dataclass(slots=True)
class Job:
    job_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    priority: Priority = Priority.MEDIUM
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: Status = Status.SCHEDULED

    # Epoch seconds. In the future means the job waits in the scheduled set.
    run_at: float = field(default_factory=time.time)

    attempt: int = 0
    max_attempts: int = 5

    # Two jobs with the same key are the same job. Without this, a caller that
    # retries an enqueue after a timeout cancels the booking twice.
    idempotency_key: str | None = None

    # Correlation, so a job can be traced back to the conversation that caused it.
    call_id: str | None = None
    customer_id: str | None = None

    # W3C trace context captured at enqueue, so the attempt that runs thirty
    # seconds later on another process lands inside the trace of the call that
    # asked for it. HTTP propagates this in headers automatically; a Redis hash
    # is just strings, so it has to be carried explicitly.
    trace_context: dict[str, str] = field(default_factory=dict)

    last_error: str | None = None
    last_error_code: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_redis(self) -> dict[str, str]:
        """Redis hashes hold strings, so everything is flattened on the way in."""
        return {
            "id": self.id,
            "job_type": self.job_type,
            "payload": json.dumps(self.payload),
            "priority": str(self.priority),
            "status": str(self.status),
            "run_at": str(self.run_at),
            "attempt": str(self.attempt),
            "max_attempts": str(self.max_attempts),
            "idempotency_key": self.idempotency_key or "",
            "call_id": self.call_id or "",
            "customer_id": self.customer_id or "",
            "trace_context": json.dumps(self.trace_context) if self.trace_context else "",
            "last_error": self.last_error or "",
            "last_error_code": self.last_error_code or "",
            "created_at": str(self.created_at),
        }

    @classmethod
    def from_redis(cls, raw: dict[str, str]) -> "Job":
        return cls(
            id=raw["id"],
            job_type=raw["job_type"],
            payload=json.loads(raw.get("payload") or "{}"),
            priority=Priority(raw.get("priority", "MEDIUM")),
            status=Status(raw.get("status", "QUEUED")),
            run_at=float(raw.get("run_at", 0) or 0),
            attempt=int(raw.get("attempt", 0) or 0),
            max_attempts=int(raw.get("max_attempts", 5) or 5),
            idempotency_key=raw.get("idempotency_key") or None,
            call_id=raw.get("call_id") or None,
            customer_id=raw.get("customer_id") or None,
            trace_context=json.loads(raw.get("trace_context") or "{}"),
            last_error=raw.get("last_error") or None,
            last_error_code=raw.get("last_error_code") or None,
            created_at=float(raw.get("created_at", 0) or 0),
        )

    @property
    def attempts_remaining(self) -> int:
        return max(0, self.max_attempts - self.attempt)
