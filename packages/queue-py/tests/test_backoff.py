"""The retry curve. Pure arithmetic — no Redis, no clock."""

from __future__ import annotations

import pytest
from voiceops_queue import backoff_seconds


def test_curve_doubles_without_jitter() -> None:
    assert [backoff_seconds(n, jitter=False) for n in range(1, 6)] == [2, 4, 8, 16, 32]


@pytest.mark.parametrize("attempt", [10, 20, 100])
def test_curve_is_capped(attempt: int) -> None:
    assert backoff_seconds(attempt, jitter=False, cap=300.0) == 300.0


def test_jitter_spreads_retries_across_the_window() -> None:
    """
    Jitter stops every job that failed in one outage from retrying in the same
    instant and knocking the recovering service over again.
    """
    samples = [backoff_seconds(4) for _ in range(200)]

    assert len(set(samples)) > 150, "jitter is not actually spreading anything"
    assert all(0 < s <= 16 for s in samples)


def test_jitter_is_equal_not_full() -> None:
    """
    Equal jitter, not full jitter.

    Full jitter draws from [0, delay] and so routinely produces near-zero waits,
    giving up the exponential growth it is layered on. Measured on a real
    cancellation against a hard-down service, it burned all five attempts in
    about five seconds — no recovery window at all. Equal jitter draws from
    [delay/2, delay], so the floor grows with the curve.
    """
    samples = [backoff_seconds(4) for _ in range(200)]

    assert min(samples) >= 8, f"min {min(samples):.2f} — that is full jitter, not equal"
