"""
Consuming the queue: reserve, acknowledge, retry, recover.

Three properties matter here, and each is bought with a specific mechanism:

**Priority.** A free worker takes the highest-priority waiting job. The reserve
script walks the ready lists in order, so an urgent callback never queues behind
a batch of follow-ups.

**At-least-once delivery.** A reserved job moves to a `processing` sorted set
scored by a lease deadline. If the worker crashes, the lease expires and the
reaper returns the job to its ready list. Nothing is lost because a process died
holding it — which is the failure a plain LPOP cannot survive.

**Atomicity.** Reserve, promote and reap are Lua scripts, so each runs as one
indivisible step. Doing "pop, then record" as two round trips leaves a window in
which a crash loses the job, and that window is exactly what the lease exists to
close, so it cannot also be the thing that opens it.

Reserving polls rather than blocking. `BLPOP` would wait more efficiently, but it
pops *before* we can record the lease, reintroducing the gap. A short poll costs
a little idle Redis traffic and buys a guarantee, which is the right trade for
work that must not vanish.
"""

from __future__ import annotations

import time

import redis.asyncio as redis

from voiceops_queue.backoff import backoff_seconds
from voiceops_queue.errors import classify
from voiceops_queue.keys import DEFAULT_NAMESPACE, Keys
from voiceops_queue.models import PRIORITY_ORDER, Job, Status

# Walk the ready lists highest priority first; the first hit wins. Recording the
# lease in the same script is what makes the reservation crash-safe.
RESERVE_LUA = """
local processing = KEYS[1]
local deadline   = tonumber(ARGV[1])
local prefix     = ARGV[2]
local worker     = ARGV[3]
local now        = ARGV[4]

for i = 5, #ARGV do
  local job_id = redis.call('LPOP', ARGV[i])
  if job_id then
    redis.call('ZADD', processing, deadline, job_id)
    redis.call('HSET', prefix .. job_id,
               'status', 'PROCESSING', 'worker_id', worker, 'started_at', now)
    return job_id
  end
end
return nil
"""

# Move every job whose time has come onto its ready list. Each job's priority is
# read from its own hash, so a scheduled HIGH job lands in the HIGH list.
PROMOTE_LUA = """
local scheduled = KEYS[1]
local now       = tonumber(ARGV[1])
local prefix    = ARGV[2]
local ns        = ARGV[3]

local due = redis.call('ZRANGEBYSCORE', scheduled, '-inf', now, 'LIMIT', 0, 500)
local moved = 0
for _, job_id in ipairs(due) do
  local priority = redis.call('HGET', prefix .. job_id, 'priority') or 'MEDIUM'
  redis.call('RPUSH', ns .. ':ready:' .. priority, job_id)
  redis.call('HSET', prefix .. job_id, 'status', 'QUEUED')
  redis.call('ZREM', scheduled, job_id)
  moved = moved + 1
end
return moved
"""

# Leases that expired belong to workers that died mid-job. Put the work back.
REAP_LUA = """
local processing = KEYS[1]
local now        = tonumber(ARGV[1])
local prefix     = ARGV[2]
local ns         = ARGV[3]

local expired = redis.call('ZRANGEBYSCORE', processing, '-inf', now, 'LIMIT', 0, 500)
local recovered = 0
for _, job_id in ipairs(expired) do
  local priority = redis.call('HGET', prefix .. job_id, 'priority') or 'MEDIUM'
  redis.call('RPUSH', ns .. ':ready:' .. priority, job_id)
  redis.call('HSET', prefix .. job_id, 'status', 'QUEUED')
  redis.call('ZREM', processing, job_id)
  recovered = recovered + 1
end
return recovered
"""


class QueueConsumer:
    def __init__(
        self,
        url: str,
        *,
        namespace: str = DEFAULT_NAMESPACE,
        worker_id: str = "worker",
        visibility_timeout: float = 60.0,
        backoff_base: float = 2.0,
        backoff_cap: float = 300.0,
    ) -> None:
        self.redis: redis.Redis = redis.from_url(url, decode_responses=True)
        self.keys = Keys(namespace)
        self.worker_id = worker_id
        self.visibility_timeout = visibility_timeout
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap

        self._reserve = self.redis.register_script(RESERVE_LUA)
        self._promote = self.redis.register_script(PROMOTE_LUA)
        self._reap = self.redis.register_script(REAP_LUA)

    async def close(self) -> None:
        await self.redis.aclose()

    @property
    def _job_prefix(self) -> str:
        return f"{self.keys.ns}:job:"

    async def reserve(self) -> Job | None:
        """Take the highest-priority waiting job, leased to this worker."""
        now = time.time()
        job_id = await self._reserve(
            keys=[self.keys.processing],
            args=[
                now + self.visibility_timeout,
                self._job_prefix,
                self.worker_id,
                now,
                *(self.keys.ready(priority) for priority in PRIORITY_ORDER),
            ],
        )
        if not job_id:
            return None

        job = Job.from_redis(await self.redis.hgetall(self.keys.job(job_id)))
        job.attempt += 1
        await self.redis.hset(self.keys.job(job_id), "attempt", str(job.attempt))
        return job

    async def extend_lease(self, job: Job) -> None:
        """Push a lease out for work that legitimately runs long."""
        await self.redis.zadd(
            self.keys.processing, {job.id: time.time() + self.visibility_timeout}
        )

    async def ack(self, job: Job, result: str | None = None) -> None:
        pipe = self.redis.pipeline()
        pipe.zrem(self.keys.processing, job.id)
        pipe.hset(
            self.keys.job(job.id),
            mapping={"status": str(Status.SUCCESS), "result": result or "",
                     "completed_at": str(time.time())},
        )
        pipe.incr(self.keys.stat("succeeded"))
        await pipe.execute()

    async def nack(
        self,
        job: Job,
        *,
        error: str,
        code: str | None = None,
        status_code: int | None = None,
        retryable_hint: bool | None = None,
    ) -> tuple[Status, float | None]:
        """
        Record a failure and decide what happens next.

        Returns the new status and, for a retry, how long the backoff was.

        Three outcomes, and which one applies is the point of the whole engine:
          FAILED      — permanent. Not retried, whatever attempts remain.
          RETRY_WAIT  — transient, attempts left. Rescheduled after a backoff.
          DEAD_LETTER — transient, attempts exhausted. Parked for a human.
        """
        verdict = classify(code=code, status_code=status_code, retryable_hint=retryable_hint)
        exhausted = job.attempt >= job.max_attempts

        common = {
            "last_error": error[:500],
            "last_error_code": code or "",
            "attempt": str(job.attempt),
        }

        pipe = self.redis.pipeline()
        pipe.zrem(self.keys.processing, job.id)

        if not verdict.retryable:
            # The most valuable branch. A booking that does not exist will not
            # start existing because we asked five times.
            pipe.hset(self.keys.job(job.id), mapping={**common, "status": str(Status.FAILED)})
            pipe.incr(self.keys.stat("failed"))
            await pipe.execute()
            return Status.FAILED, None

        if exhausted:
            pipe.hset(self.keys.job(job.id), mapping={**common, "status": str(Status.DEAD_LETTER)})
            pipe.rpush(self.keys.dlq, job.id)
            pipe.incr(self.keys.stat("dead_lettered"))
            await pipe.execute()
            return Status.DEAD_LETTER, None

        delay = backoff_seconds(job.attempt, base=self.backoff_base, cap=self.backoff_cap)
        run_at = time.time() + delay
        pipe.hset(
            self.keys.job(job.id),
            mapping={**common, "status": str(Status.RETRY_WAIT), "run_at": str(run_at)},
        )
        pipe.zadd(self.keys.scheduled, {job.id: run_at})
        pipe.incr(self.keys.stat("retried"))
        await pipe.execute()
        return Status.RETRY_WAIT, delay

    async def promote_due(self) -> int:
        """Move scheduled jobs whose time has come onto the ready lists."""
        return int(
            await self._promote(
                keys=[self.keys.scheduled],
                args=[time.time(), self._job_prefix, self.keys.ns],
            )
        )

    async def reap_expired(self) -> int:
        """Recover jobs whose worker died holding the lease."""
        recovered = int(
            await self._reap(
                keys=[self.keys.processing],
                args=[time.time(), self._job_prefix, self.keys.ns],
            )
        )
        if recovered:
            await self.redis.incrby(self.keys.stat("reaped"), recovered)
        return recovered
