# Platform API

The only public entry point. Owns authentication and RBAC, reads calls, jobs and
analytics from Postgres, talks to the queue, and proxies voice turns to the AI
service — which is never exposed directly.

Fastify + TypeScript, `services/api/`. Start with `make api` (port 3000).

## Why everything goes through here

The AI service holds the confirmation gate (ADR 0004). An internet-reachable
`/v1/turn` would be a way around authentication entirely: anyone who could reach
it could cancel bookings. So it stays internal, and this API is the front door.

```
browser ──► Node API ──► PostgreSQL
              │    └───► Redis (queue)
              └────────► AI service ──► flight service
                          (internal)
```

## Auth

```bash
curl -X POST localhost:3000/api/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"supervisor@voiceops.ai","password":"voiceops123"}'
```

Returns a JWT plus the caller's permissions. Send it as `Authorization: Bearer <token>`.

Login failures return one code, `INVALID_CREDENTIALS`, for a wrong password, an
unknown email and a deactivated account alike. Distinguishing them tells an
attacker which addresses are real.

## Roles

Defined once in [`src/auth/rbac.ts`](../services/api/src/auth/rbac.ts) as a table,
not as scattered `if (role === ...)` checks — those drift, and a permission
quietly attached to the wrong role reads perfectly fine.

| Permission | USER | CX_AGENT | SUPERVISOR | ADMIN |
|---|:--:|:--:|:--:|:--:|
| `voice:use` | ● | ● | ● | ● |
| `calls:read` | | ● | ● | ● |
| `conversations:read` | | ● | ● | ● |
| `calls:escalate` | | ● | ● | ● |
| `jobs:read` | | | ● | ● |
| `jobs:retry` | | | ● | ● |
| `failures:read` | | | ● | ● |
| `analytics:read` | | | ● | ● |
| `users:manage` | | | | ● |
| `config:manage` | | | | ● |

`make test-rbac` checks all 40 combinations, that privilege only increases up the
hierarchy, and a handful of denials that would matter most if they were ever wrong.

401 means "log in"; 403 means "you cannot do this". Keeping them distinct matters —
collapsing both into one status makes each harder to debug.

## Endpoints

| Method | Path | Permission |
|---|---|---|
| `POST` | `/api/auth/login` | — |
| `GET` | `/api/auth/me` | `voice:use` |
| `GET` | `/api/calls` | `calls:read` |
| `GET` | `/api/calls/:id` | `calls:read` (transcript needs `conversations:read`) |
| `POST` | `/api/calls/:id/escalate` | `calls:escalate` |
| `GET` | `/api/jobs` | `jobs:read` |
| `GET` | `/api/jobs/dead-letters` | `jobs:read` |
| `GET` | `/api/jobs/:id` | `jobs:read` |
| `POST` | `/api/jobs` | `jobs:retry` |
| `POST` | `/api/jobs/:id/retry` | `jobs:retry` |
| `GET` | `/api/analytics/calls` | `analytics:read` |
| `GET` | `/api/analytics/failures` | `failures:read` |
| `GET` | `/api/analytics/queue` | `jobs:read` |
| `POST` | `/api/voice/turn` | `voice:use` |
| `POST` | `/api/voice/turn/audio` | `voice:use` |

`GET /api/calls/:id` returns the transcript only if the caller has
`conversations:read`: a supervisor reviewing queue health has no need to read what
customers said.

## Notes

- **One error envelope**, matching the AI and flight services:
  `{"error": {code, message, retryable}}`. A caller should never have to branch on
  which service produced a failure.
- **Aggregates are computed in SQL**, not by pulling rows into Node. Postgres has
  the indexes, and shipping ten thousand calls over the wire to produce five
  numbers stops working at exactly the volume a dashboard is for.
- **Filters are bound parameters**, never interpolated. A status of
  `'; DROP TABLE calls; --` is simply a status that matches nothing.
- **Audio is forwarded as opaque bytes.** Decoding a multipart upload only to
  re-encode it would waste memory per turn and risk corrupting the recording, for
  no gain — this layer has no business looking inside it.
- **The queue client here is a deliberate subset**: enqueue, inspect, requeue.
  Reservation, leases, the reaper and backoff stay in the Python worker. Two
  implementations of a distributed algorithm is two chances to get it subtly
  different, and the differences would only appear under the failures it exists to
  survive.
- **`AI_SERVICE_URL` uses an IP, not `localhost`.** Node 18+ resolves `localhost`
  to `::1` first, and a server bound to `127.0.0.1` never answers that — it
  surfaces as an unhelpful `fetch failed`.
