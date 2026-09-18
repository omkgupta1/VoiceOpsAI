# 0006 — A queued retry is a child span of the call that caused it

**Status:** Accepted (Phase 9)

## Context

A voice call can hand work to the queue: a confirmed cancellation that fails for
a transient reason is queued rather than lost (ADR 0004, Phase 6). That job may
then be retried four more times over half a minute, in a different process, long
after the HTTP request that created it has returned.

Trace context does not cross Redis on its own. HTTP propagation is automatic —
the `traceparent` header rides along with every request — but a job is a Redis
hash of strings, so the context has to be written into the job at enqueue and
read back when a worker reserves it. Once it is carried, there is a real choice
about what to do with it.

OpenTelemetry's own guidance for queues is **span links**: each attempt becomes
its own trace, carrying a link back to the trace that produced it. The reasoning
is sound — traces stay small, they close promptly, and a batch consumer handling
messages from many producers has no single sensible parent.

## Decision

Attempts are **children of the originating call's trace**, not linked siblings.
`Job.trace_context` holds the W3C headers captured at enqueue, and the worker
starts each attempt span with that as its explicit parent.

This project's shape is not the one the spec's advice is aimed at. Every job here
has exactly one cause — a specific conversation — and the question actually being
asked of a trace is "what happened to the thing this customer asked for", whose
answer spans the call *and* every retry. Split across five traces joined by
links, that answer requires five clicks to assemble.

## Consequences

**What this buys.** One flame graph holds the whole story. A verified run shows
88 spans under one root: the call, the model choosing `cancel_booking`, the
in-call failure against a hard-down service, five worker attempts with the
measured backoff on each (1884ms → 3155ms → 5384ms → 14659ms), the dead-letter,
an operator's requeue from the dashboard, and the final success — the `UPDATE`
that actually cancelled the booking.

**What it costs, stated plainly:**

- **A trace is incomplete until the retries finish.** Opened ten seconds in, it
  shows a partial picture and needs a refresh. Jaeger assembles spans by trace id
  as they arrive, so nothing is lost — but the view lies by omission until it is
  reloaded.
- **A scheduled callback attaches to a trace nobody is watching.** A job due in
  two hours will land inside a trace last looked at two hours ago. It is
  correct and findable, but it is not a notification.
- **A dead-lettered job contributes five failed spans to its parent trace**, so
  the call reads as failing even though the customer was answered correctly. The
  span attributes distinguish them; the top-line colour does not.
- **This does not generalise.** A batch consumer draining a queue filled by many
  producers has no single parent to attach to, and forcing one would join
  unrelated work into a trace that means nothing. Links are right there. The
  decision here rests on one job having exactly one cause.

**Reversing it** is a small change — start the attempt span with no parent and
add a link to `context_from(job.trace_context)` instead. The context is carried
either way; only the relationship changes.
