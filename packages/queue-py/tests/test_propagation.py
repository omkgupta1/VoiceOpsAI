"""
Trace context across the queue.

The important property here is not that propagation works — it is that the queue
package still works when the telemetry package is absent. `test_queue.py` runs
against a bare Redis with no observability installed, and a library that cannot
be tested in isolation is one that stops being tested.
"""

from __future__ import annotations

from voiceops_queue import Job, current_carrier, parent_context


def test_carrier_is_a_dict_either_way() -> None:
    """
    With telemetry installed and inside a span this returns W3C headers; without
    it, an empty dict. Never None, so callers never branch on availability.
    """
    assert isinstance(current_carrier(), dict)


def test_parent_context_tolerates_nothing_to_extract() -> None:
    assert parent_context(None) is None
    assert parent_context({}) is None


def test_parent_context_does_not_raise_on_a_malformed_header() -> None:
    """
    `trace_context` comes out of Redis, where anything could have written it.
    A corrupt traceparent must degrade to no parent, not take down the worker
    that was about to run the job.
    """
    parent_context({"traceparent": "not-a-valid-traceparent"})


def test_context_survives_the_redis_round_trip() -> None:
    headers = {"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"}
    job = Job(job_type="cancel_booking", trace_context=headers)

    assert Job.from_redis(job.to_redis()).trace_context == headers
