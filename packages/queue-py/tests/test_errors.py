"""
Error classification: the single decision that separates a retry from a failure.

Getting this wrong is expensive in both directions. Retrying a permanent error
spends four more attempts and a customer's patience on a request that cannot
succeed; failing a transient one throws away work that would have gone through a
second later.
"""

from __future__ import annotations

import pytest
from voiceops_queue.errors import classify


@pytest.mark.parametrize(
    ("kwargs", "retryable", "why"),
    [
        ({"status_code": 503}, True, "service unavailable is transient by definition"),
        ({"status_code": 429}, True, "rate limited — back off and come back"),
        ({"status_code": 500}, True, "a server fault may not recur"),
        ({"status_code": 404}, False, "the thing is not there and will not appear"),
        ({"status_code": 422}, False, "we asked wrongly; asking again changes nothing"),
        ({"status_code": 400}, False, "malformed stays malformed"),
        ({"code": "BOOKING_NOT_FOUND"}, False, "a known permanent code"),
        ({"code": "UPSTREAM_TIMEOUT"}, True, "a known transient code"),
        ({"code": "SERVICE_UNAVAILABLE"}, True, "a known transient code"),
        ({}, True, "unclassified is retried, bounded by the attempt cap"),
    ],
)
def test_classification(kwargs: dict, retryable: bool, why: str) -> None:
    assert classify(**kwargs).retryable is retryable, why


def test_status_code_beats_a_lying_upstream_hint() -> None:
    """
    Precedence: known code > HTTP status > the upstream's own `retryable` hint.

    A 404 that claims to be retryable is still not retried. The upstream is
    describing its own mood; the status describes what we asked for, and we
    asked for something that does not exist.
    """
    assert classify(status_code=404, retryable_hint=True).retryable is False


def test_a_known_code_beats_the_status() -> None:
    assert classify(code="BOOKING_NOT_FOUND", status_code=503).retryable is False


def test_classification_explains_itself() -> None:
    """Every verdict carries a reason — it is logged, and read during incidents."""
    assert classify(status_code=503).reason
