# Queue protocol

The Redis layout, written down because more than one service speaks it: the AI
service enqueues, the worker consumes, and the Node API (Phase 7) will do both.
A queue whose layout lives only in the code that writes it is a queue nobody can
inspect when it misbehaves.

Implementation: [`packages/queue-py/`](../packages/queue-py/).

## Keys

| Key | Type | Holds |
|---|---|---|
| `{ns}:job:{id}` | HASH | the job itself |
| `{ns}:ready:{HIGH\|MEDIUM\|LOW}` | LIST | job ids waiting, one list per priority |
| `{ns}:scheduled` | ZSET | job id → epoch seconds it becomes due |
| `{ns}:processing` | ZSET | job id → epoch seconds its lease expires |
| `{ns}:dlq` | LIST | job ids that exhausted their attempts |
| `{ns}:idem:{key}` | STRING | idempotency key → job id |
| `{ns}:stats:{name}` | STRING | lifetime counters |

`{ns}` is `QUEUE_NAMESPACE` (default `vo`), so one Redis can host several
environments.

## Lifecycle

```
                enqueue
                   │
        run_at ────┴──── run_at
        now            future
          │               │
          ▼               ▼
      ready:{pri}     scheduled ──scheduler promotes when due──┐
          │  ▲                                                 │
          │  └─────────────────────────────────────────────────┘
       reserve
          │
          ▼
      processing ──lease expires (worker died)──► ready:{pri}
          │
     ┌────┴─────┬──────────────┐
     ▼          ▼              ▼
  SUCCESS   RETRY_WAIT       FAILED            DEAD_LETTER
            (scheduled)   (permanent —      (transient, attempts
                           never retried)     exhausted)
```

## The three guarantees, and what buys each

**Priority.** `reserve` walks the ready lists highest-first and takes the first
hit, so an urgent callback never queues behind a batch of follow-ups.

**At-least-once delivery.** A reserved job moves to `processing`, scored by a
lease deadline. If its worker dies, the lease expires and the reaper returns the
job to its ready list. Nothing is lost because a process died holding it — the
failure a plain `LPOP` cannot survive.

**Atomicity.** `reserve`, `promote` and `reap` are Lua scripts, each one
indivisible step. "Pop, then record the lease" as two round trips leaves a window
where a crash loses the job — and that window is precisely what the lease exists
to close, so it cannot also be the thing that opens it.

> Reserving **polls** rather than blocking. `BLPOP` waits more efficiently but
> pops *before* the lease can be recorded, reintroducing the gap. A short poll
> costs some idle Redis traffic and buys a guarantee.

## Retry policy

On failure, `nack` classifies and picks one of three outcomes:

| Outcome | When | What happens |
|---|---|---|
| `FAILED` | **permanent** error | Not retried, however many attempts remain |
| `RETRY_WAIT` | transient, attempts left | Rescheduled after a backoff |
| `DEAD_LETTER` | transient, attempts exhausted | Parked for a human |

Classification precedence is deliberate: a known error **code** beats a **status
code**, which beats the upstream's own `retryable` **hint**. The hint is trusted
last because it is the thing most likely to be absent or wrong — *a 404 that
claims to be retryable is still a 404.*

### Backoff: equal jitter, not full jitter

`delay = min(base^attempt, cap)`, then a random draw from `[delay/2, delay]`.

Full jitter — `uniform(0, delay)` — is the more commonly cited form, and it was
the first implementation. Watching a real cancellation retry against a hard-down
flight service showed the problem: waits of 0.6s, 1.2s, 1.1s burned all five
attempts in about five seconds. That is not backing off, it is hammering with
extra steps.

Equal jitter keeps both properties: every wait is at least half the intended
backoff, so the curve still grows, while the random half still scatters jobs that
all failed during the same outage. Measured after the change, against the same
outage: **1.1s → 3.5s → 4.1s → 12.1s**, and the fifth attempt succeeded.

## Idempotency

`enqueue` with an `idempotency_key` does `SET NX`; whoever sets it first owns the
job, and a second enqueue returns the original rather than creating a duplicate.
This is what stops a caller that retried after a timeout from cancelling the same
booking twice (overview §1). Keys expire after 24 hours.

The AI service derives its key from the call and the operation
(`{call_id}:{tool}:{pnr}`), so a customer who repeats themselves produces one job.

## Redis holds work, Postgres holds history

The worker mirrors every job, attempt and failure into Postgres. Redis forgets a
successful job entirely; "how often does the flight service make us retry" is
exactly what the dashboard and the Phase 12 alarms need answered.

Mirroring is **best-effort**. A database hiccup must never cost a job: the queue
is the system of record for *work*, Postgres for *history*, and losing a history
row is survivable in a way that losing a cancellation is not.

## Commands

```bash
make worker          # run workers + scheduler
make queue           # depth, counters, dead letters   (W=1 to watch)
make test-queue      # priority, crash recovery, backoff, DLQ, idempotency

cd packages/queue-py && uv run python ../../scripts/enqueue.py \
    cancel_booking --pnr ABC123 --priority HIGH
```

## Verified behaviour

- **Priority**: enqueued LOW, MEDIUM, HIGH, LOW → served HIGH, MEDIUM, LOW, LOW
- **Crash recovery**: `kill -9` the only worker holding a job; a fresh scheduler
  logs `recovered 1 job(s) from expired leases` and another worker completes it
  as attempt 2
- **Survives an outage**: customer confirms a cancellation, the flight service is
  taken hard down, the job retries 1.1s → 3.5s → 4.1s → 12.1s and succeeds on the
  fifth attempt once the service returns. The customer was told it was in hand
  and did not need to call back.
- **Permanent errors** fail on the first attempt with attempts unspent
