# 0003 — Build the Redis queue by hand instead of using BullMQ

**Status:** Accepted (applies from Phase 6)

## Context

BullMQ (Node) and Celery/RQ (Python) already provide priority queues, delayed jobs,
retries with backoff and dead-letter handling. Using one would save more than a day.

## Decision

Implement the queue directly on Redis primitives — per-priority lists, a processing ZSET
with visibility timeouts, a scheduled-jobs ZSET scored by `run_at`, and an explicit retry
engine. The protocol is documented in `docs/queue-protocol.md` so both Python and Node
can speak it.

## Consequences

- The reliability mechanics this project exists to teach — crash recovery, at-least-once
  delivery, idempotency, backoff with jitter, DLQ policy — stay visible instead of being
  hidden behind a library. This is the primary reason.
- Full control over priority semantics and observability hooks; we can export exactly the
  metrics the dashboard and CloudWatch alarms need.
- Cost: we own the bugs. Mitigated by making crash recovery an explicit, tested scenario
  (`kill -9` a worker mid-job and watch the job come back).
- Not a recommendation for production. In production, use the battle-tested library.
