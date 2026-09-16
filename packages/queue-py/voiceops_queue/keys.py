"""
The Redis key schema.

Written down in one place because two services speak it — the AI service enqueues,
the worker consumes — and a queue whose layout lives only in the code that writes
it is a queue nobody else can inspect when it misbehaves.

    vo:job:{id}            HASH   the job itself
    vo:ready:{priority}    LIST   job ids waiting, one list per priority
    vo:scheduled           ZSET   job id -> epoch seconds it becomes due
    vo:processing          ZSET   job id -> epoch seconds its lease expires
    vo:dlq                 LIST   job ids that exhausted their attempts
    vo:idem:{key}          STRING idempotency key -> job id
    vo:stats:{name}        STRING counters, for the dashboard

Everything is namespaced so one Redis can host several environments.
"""

from __future__ import annotations

DEFAULT_NAMESPACE = "vo"


class Keys:
    def __init__(self, namespace: str = DEFAULT_NAMESPACE) -> None:
        self.ns = namespace

    def job(self, job_id: str) -> str:
        return f"{self.ns}:job:{job_id}"

    def ready(self, priority: str) -> str:
        return f"{self.ns}:ready:{priority}"

    @property
    def scheduled(self) -> str:
        return f"{self.ns}:scheduled"

    @property
    def processing(self) -> str:
        return f"{self.ns}:processing"

    @property
    def dlq(self) -> str:
        return f"{self.ns}:dlq"

    def idempotency(self, key: str) -> str:
        return f"{self.ns}:idem:{key}"

    def stat(self, name: str) -> str:
        return f"{self.ns}:stats:{name}"
