"""
Fixtures for the queue tests.

Redis is real, never mocked. The properties under test here — atomic
reservation, lease expiry, priority ordering — *are* Redis semantics, and a mock
would only assert that the mock behaves as I imagined Redis does.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from voiceops_queue import QueueClient, QueueConsumer

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# A namespace of its own, so a test run can never touch the jobs of a worker
# someone has running on the same machine.
NAMESPACE = "votest"


@pytest_asyncio.fixture
async def queue():
    """
    A purged queue, and a consumer with a deliberately short lease.

    The factory takes `backoff_base` because one test needs a fast curve to stay
    quick while another asserts the real one.
    """
    opened: list = []

    async def make(backoff_base: float = 2.0) -> tuple[QueueClient, QueueConsumer]:
        client = QueueClient(REDIS_URL, namespace=NAMESPACE)
        await client.purge()
        consumer = QueueConsumer(
            REDIS_URL,
            namespace=NAMESPACE,
            worker_id="test",
            # One second, so crash recovery can be tested without a long sleep.
            visibility_timeout=1.0,
            backoff_base=backoff_base,
        )
        opened.extend([client, consumer])
        return client, consumer

    yield make

    for handle in opened:
        await handle.close()


def pytest_collection_modifyitems(config, items):
    """
    Skip the Redis tests with a useful reason when Redis is not running.

    A connection timeout thirty seconds into a test run tells you nothing; this
    says which command to run. Checked once, at collection, rather than per test.
    """
    if config.getoption("markexpr") == "not integration":
        return

    import redis

    try:
        redis.from_url(REDIS_URL).ping()
        return
    except Exception as exc:
        reason = f"Redis unreachable at {REDIS_URL} — run `make up` ({type(exc).__name__})"

    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
