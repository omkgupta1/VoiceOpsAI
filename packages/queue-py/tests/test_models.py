"""
Job serialisation.

A Redis hash holds strings and nothing else, so every field makes a round trip
through `str` and back. The failure this guards against is quiet: a field that
serialises but does not deserialise reads back as a default, and the job simply
behaves as though it was never configured.
"""

from __future__ import annotations

import time

from voiceops_queue import Job, Priority, Status


def test_round_trip_preserves_every_field() -> None:
    original = Job(
        job_type="cancel_booking",
        payload={"pnr": "ABC123", "nested": {"count": 2}},
        priority=Priority.HIGH,
        status=Status.QUEUED,
        run_at=time.time() + 30,
        attempt=2,
        max_attempts=7,
        idempotency_key="cancel:ABC123",
        call_id="call-1",
        customer_id="cust-1",
        trace_context={"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"},
        last_error="upstream is down",
        last_error_code="SERVICE_UNAVAILABLE",
    )

    restored = Job.from_redis(original.to_redis())

    assert restored.id == original.id
    assert restored.job_type == original.job_type
    assert restored.payload == original.payload
    assert restored.priority is Priority.HIGH
    assert restored.attempt == 2
    assert restored.max_attempts == 7
    assert restored.idempotency_key == "cancel:ABC123"
    assert restored.call_id == "call-1"
    assert restored.customer_id == "cust-1"
    assert restored.trace_context == original.trace_context
    assert restored.last_error_code == "SERVICE_UNAVAILABLE"
    assert restored.run_at == original.run_at


def test_empty_optionals_come_back_as_none_not_empty_string() -> None:
    """
    Redis cannot store None, so absent fields go in as "".

    Reading them back as "" rather than None would make `if job.call_id` behave
    the same but `job.call_id == None` silently false, and a payload of `""`
    would be parsed as a dict somewhere downstream.
    """
    restored = Job.from_redis(Job(job_type="scheduled_callback").to_redis())

    assert restored.idempotency_key is None
    assert restored.call_id is None
    assert restored.customer_id is None
    assert restored.last_error is None
    assert restored.trace_context == {}


def test_a_job_written_before_tracing_existed_still_loads() -> None:
    """
    Jobs already in Redis when this field was added have no `trace_context` key
    at all. Deserialising must not raise — an upgrade that cannot read the queue
    it inherits is an outage.
    """
    raw = Job(job_type="cancel_booking").to_redis()
    del raw["trace_context"]

    assert Job.from_redis(raw).trace_context == {}


def test_attempts_remaining_never_goes_negative() -> None:
    assert Job(job_type="x", attempt=9, max_attempts=5).attempts_remaining == 0
    assert Job(job_type="x", attempt=2, max_attempts=5).attempts_remaining == 3
