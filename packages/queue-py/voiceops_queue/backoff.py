"""
Exponential backoff with jitter.

The exponential part spaces retries so a struggling service gets room to recover
(overview.md §9). The jitter matters just as much and is easier to leave out:
without it, every job that failed during the same outage retries at the same
instant, and the recovering service is knocked over by its own backlog. That is
a thundering herd, and it turns one outage into two.

Two jitter strategies were tried. **Full jitter** — a uniform draw from
[0, delay] — spreads retries widest, but it routinely draws near-zero waits and
so gives up the exponential part it is layered on top of. Watching a real
cancellation retry against a hard-down service, it burned all five attempts in
about five seconds: 0.6s, 1.2s, 1.1s. That is not backing off, it is hammering
with extra steps.

**Equal jitter** is used instead: half the delay, plus a random draw from the
other half. Every wait is at least half the intended backoff, so the curve still
grows, while the random half still scatters a herd of jobs that all failed during
the same outage.
"""

from __future__ import annotations

import random


def backoff_seconds(
    attempt: int,
    *,
    base: float = 2.0,
    cap: float = 300.0,
    jitter: bool = True,
) -> float:
    """
    How long to wait before `attempt` (1-based: attempt 1 is the first retry).

    Without jitter this is the curve from overview.md §9 — 2s, 4s, 8s, 16s, 32s —
    capped so a long-lived job cannot schedule itself hours away.

    With jitter (the default), the result lies in [delay/2, delay]: still growing
    exponentially, still scattered enough to avoid a thundering herd.
    """
    if attempt < 1:
        return 0.0

    delay = min(base ** attempt, cap)
    if not jitter:
        return delay

    half = delay / 2
    return half + random.uniform(0, half)
