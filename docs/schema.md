# Database schema

Defined in [`db/migrations/`](../db/migrations/) as raw SQL — see
[ADR 0002](decisions/0002-raw-sql-migrations.md) for why no ORM owns it.

```
customers ──< bookings >── flights
    │            │
    │            └──< refunds
    │
    └──< calls ──< conversations
           │
           ├──< jobs ──< job_attempts
           │      │
           │      └──< failures
           │
           └──< support_tickets >── users
```

## Tables

| Table | Holds | Notes |
|---|---|---|
| `customers` | People who call in | `phone` is the natural key — it identifies a caller before they speak |
| `flights` | Schedule and live status | Unique per `(flight_number, scheduled_departure)`; a number repeats daily |
| `bookings` | PNR, fare, refundability | `is_refundable` drives cancellation eligibility |
| `refunds` | Refund lifecycle | One per cancelled refundable booking |
| `calls` | One voice interaction | `duration_ms` is a generated column, derived by Postgres |
| `conversations` | One row per turn | Carries `stt_ms` / `llm_ms` / `tts_ms` and which providers served the turn |
| `jobs` | Async work, current state | `idempotency_key` prevents duplicate execution |
| `job_attempts` | One row per attempt | The retry story: backoff, duration, error class |
| `failures` | Operator-visible failures | Can exist without a job (e.g. STT died mid-call) |
| `users` | Dashboard staff | Distinct from `customers`, who never log in |
| `support_tickets` | Tickets raised from calls | Assigned to a `user` |

## The distinction that matters most

`error_class` is either `RETRYABLE` or `PERMANENT`, and the difference governs
everything downstream:

- **RETRYABLE** — timeout, 503, 429, connection reset. Earns exponential backoff
  and another attempt: 0s, 2s, 4s, 8s, 16s.
- **PERMANENT** — booking not found, not cancellable, unauthorized. Fails
  immediately with **exactly one attempt**. Retrying cannot help and only
  hammers a service that is answering correctly.

The seed data enforces this, and two invariant queries check it:

```sql
-- Both must return 0.
SELECT count(*) FROM job_attempts WHERE error_class='PERMANENT' AND attempt_number > 1;

SELECT count(*) FROM (
  SELECT job_id FROM job_attempts GROUP BY job_id
  HAVING bool_or(error_class='PERMANENT') AND count(*) > 1
) t;
```

## Job lifecycle

```
SCHEDULED ──► QUEUED ──► PROCESSING ──► SUCCESS
                 ▲            │
                 │            ▼
            RETRY_WAIT ◄── FAILED ──► DEAD_LETTER   (retries exhausted)
                                └───► FAILED        (permanent error, 1 attempt)
```

Redis holds the *live* queue (Phase 6); these tables hold the history Redis
deliberately forgets.
