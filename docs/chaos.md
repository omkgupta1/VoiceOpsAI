# The chaos engine

The mock flight service can be broken on demand. This is not a gimmick — it is
the only way the retry, backoff and remediation machinery in later phases can be
exercised for real. A retry engine that has never seen a failure is untested code.

## Quick use

```bash
make chaos S=flaky        # 40% of requests fail with 503
make chaos-status         # current config + how many failures were injected
make chaos-off            # back to healthy
```

## Scenarios

| Scenario | Behaviour | What it exercises |
|---|---|---|
| `healthy` | Nothing injected | Baseline |
| `flaky` | 40% fail with 503 | Retries that eventually succeed |
| `hard_down` | 100% fail with 503 | Exhausting attempts → dead-letter |
| `slow` | 2500ms ± 1500ms jitter | Client timeouts, queue backpressure |
| `rate_limited` | 70% fail with 429 + `Retry-After: 2` | Honouring a server's backoff hint |
| `timeouts` | 50% hang, then 504 | The client's own read timeout firing |
| `corrupt` | 50% return **HTTP 200** with a wrong-shaped body | Response validation |

`corrupt` is the most valuable one. A client that only checks status codes sails
straight past it and fails somewhere further downstream, with a stack trace that
points at the wrong thing.

## Runtime control

Chaos state lives in memory and changes without a restart, so you can break the
service while watching a queue drain.

```bash
# Change one knob
curl -X PATCH localhost:8002/admin/chaos \
  -H 'content-type: application/json' -d '{"enabled":true,"error_rate":0.25}'

# Break one endpoint, leave the rest healthy
curl -X PUT localhost:8002/admin/chaos \
  -H 'content-type: application/json' -d '{
    "enabled": true, "error_rate": 0.0,
    "endpoints": { "/v1/bookings": { "enabled": true, "error_rate": 1.0, "error_kind": "503" } }
  }'
```

Endpoint keys are path prefixes; the longest match wins.

## Two rules the engine guarantees

**`/health` and `/admin/*` are never injected.** If `hard_down` could break the
admin API, there would be no way to turn it off again.

**Injected responses carry `x-chaos-injected`.** A failure in a log can always be
traced to a deliberate injection rather than a real bug.

## Error taxonomy

Every failure is explicitly one of two kinds, because the difference decides
whether retrying is correct or harmful.

| | Retryable | Permanent |
|---|---|---|
| Meaning | The service could not serve a valid request right now | The request is wrong and always will be |
| Response | Retry after backoff | Fail immediately, never retry |
| Examples | `SERVICE_UNAVAILABLE`, `RATE_LIMITED`, `UPSTREAM_TIMEOUT` | `BOOKING_NOT_FOUND`, `NOT_CANCELLABLE`, `ALREADY_CANCELLED`, `UNAUTHORIZED` |
| Status codes | 429, 5xx | 4xx (except 429) |

Every error leaves the service in one envelope:

```json
{"error": {"code": "SERVICE_UNAVAILABLE", "message": "...", "retryable": true}}
```

> The `retryable` flag is a convenience this service chooses to offer. The Phase 6
> retry engine must still classify from the status code alone, because real
> upstreams rarely tell you.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| `GET` | `/v1/flights/{flight_number}/status` | Resolves to the departure nearest now |
| `GET` | `/v1/bookings/{pnr}` | Full booking with passenger and flight |
| `POST` | `/v1/bookings/{pnr}/cancel` | Creates a refund if the fare is refundable |
| `GET` | `/v1/bookings/{pnr}/reschedule-options` | Up to 5 alternatives on the same route |
| `POST` | `/v1/bookings/{pnr}/reschedule` | Same route only |
| `GET` | `/v1/refunds/{reference}` | Refund status |
| `GET` | `/v1/bookings/{pnr}/refunds` | All refunds for a booking |

Interactive docs: http://localhost:8002/docs

## Business rules worth knowing

These exist so the voice agent can be told "no" by the backend. An agent that
cannot be refused will happily cancel something it should not have.

- Cancellation closes **2 hours before departure** (`CANCELLATION_CUTOFF_HOURS`).
- A cancelled booking cannot be cancelled again.
- A flown booking cannot be cancelled.
- Rescheduling changes **when** you fly, never **where** — cross-route moves are refused.
- Only `CONFIRMED` bookings can be rescheduled.
