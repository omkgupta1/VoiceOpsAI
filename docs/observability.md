# Observability

Three questions, three tools, one id that joins them.

| Question | Tool | Where |
|---|---|---|
| What happened in *this* call? | Jaeger | http://localhost:16686 |
| Is this normal? | Prometheus | http://localhost:9090 |
| Show me both at once | Grafana | http://localhost:3002 |

```bash
make up          # brings up Jaeger, Prometheus and Grafana with the rest
make test-trace  # proves one call spans every service, in one command
make metrics     # what each service is currently exposing
make traces      # open Jaeger
make grafana     # open the dashboard
```

## The one thing to look at first

`make test-trace` sends a real turn and prints the trace. A typical result:

```
api          POST                                    10294ms
  ai           POST /v1/turn                         10265ms
    ai           llm.complete                         6748ms   ← choosing the tool
    ai           tool check_flight_status               74ms
      flight-mock  GET /v1/flights/{flight_number}/status  29ms
        flight-mock  SELECT                               18ms
    ai           llm.complete                         3395ms   ← writing the answer
```

**10.1 of those 10.3 seconds are the model.** The flight lookup this whole
platform exists to perform is 30 milliseconds of it. Every instinct to optimise
should be checked against that ratio first.

## What is instrumented, and by whom

Auto-instrumentation covers the plumbing; the spans that carry meaning are
written by hand. The split is deliberate — the auto spans are the ones nobody
learns anything from writing, and they are also the ones most likely to be
forgotten.

| Automatic | Hand-written |
|---|---|
| HTTP server + client (FastAPI, Fastify, httpx, Node http) | `stt.transcribe` — with the transcript on the span |
| Every SQL statement (psycopg, pg) | `llm.complete` — provider, model, tools offered, tools requested |
| Every Redis command | `tool <name>` — arguments, ok, error code, retryable |
| | `tts.synthesize` |
| | `job <type>` — attempt, backoff, queue wait, worker id |

`stt.text` is on the span on purpose. The most common failure in this pipeline is
the agent acting correctly on a *misheard* flight number; without the transcript
the trace shows a confident answer to a question nobody asked.

## Correlation

`trace_id` is stamped on every log line in all four services, and returned to
callers as the `x-trace-id` response header. One id takes you from a log line, to
the trace, to the call in the dashboard.

In the console format it is the short id in brackets at the end of the line:

```
02:35:12 WARNING worker  job=5f389011 attempt=4/5 FAILED — retrying in 14.7s  [e81797ff]
```

`LOG_FORMAT=json` switches to structured output for a log shipper. Colour and
alignment matter when you are watching a terminal; they are noise to Loki.

## Trace context across the queue

HTTP propagates trace context in headers by itself. Redis does not — a job is a
hash of strings — so `Job.trace_context` carries the W3C headers captured at
enqueue, and the worker starts each attempt with that as its parent.

The consequence is that a retry storm is **one trace**, not six: the call, every
attempt, the backoff between them, the dead-letter and any requeue. See
[ADR 0006](decisions/0006-queue-retries-as-child-spans.md) for why this goes
against OpenTelemetry's default advice, and what it costs.

`voiceops_queue` keeps working with no telemetry installed — `propagation.py`
degrades to returning nothing. `test_queue.py` runs against a bare Redis, and a
library that cannot be tested in isolation is one that stops being tested.

## Metrics

Every metric is declared in one file, `packages/telemetry-py/voiceops_telemetry/metrics.py`.
Metric names are a public interface: dashboards and alerts are written against
them, and a name that drifts breaks a dashboard silently, weeks later.

| Metric | Answers |
|---|---|
| `voiceops_voice_stage_seconds` | which stage is slow (it is the LLM) |
| `voiceops_turns_total` | answered vs escalated vs failed |
| `voiceops_tool_calls_total` | which tools the model actually reaches for |
| `voiceops_confirmation_gate_total` | how often consent is enforced |
| `voiceops_queue_depth` | backlog — the signal KEDA will scale on in Phase 11 |
| `voiceops_jobs_total` | success / retry / dead-letter rates |
| `voiceops_job_backoff_seconds` | the backoff curve, measured not asserted |
| `voiceops_upstream_errors_total` | retryable vs permanent, by service |
| `voiceops_chaos_injections_total` | failures broken on purpose, by kind |

Buckets are chosen for this system rather than copied from a template. The
default Prometheus buckets top out at 10s, which would put nearly every local
7B-model call in the last bucket and make p95 meaningless.

### Cardinality

API request metrics are labelled by **route template** (`/api/calls/:id`), never
by URL. The raw URL would mint a new time series per call id and eventually take
Prometheus down — a failure mode common enough to have a name, cardinality
explosion, and the usual way a first metrics rollout goes wrong.

## Layout

```
packages/telemetry-py/          shared by the three Python services
  voiceops_telemetry/
    tracing.py                  setup, span helpers, queue propagation
    logs.py                     trace_id on every log record
    metrics.py                  every metric, declared once
services/api/src/telemetry.ts   the Node equivalent
infra/observability/
  prometheus.yml                scrape config
  grafana/provisioning/         datasources + dashboard provider
  grafana/dashboards/           the overview dashboard, as JSON in git
```

The Grafana dashboard is provisioned from a file in git rather than clicked
together in the UI, so it survives `make clean` and arrives with the repo.

## Notes

- **The flight service vendors the telemetry package.** It is the one service
  that runs inside the compose network, so it cannot reach a path outside its
  build context. `make up` runs `make vendor` to sync the copy, keeping one
  source of truth. The copy is gitignored.
- **Each auto-instrumentation is imported inside its own guard.** These packages
  import the library they patch at module level, so a single import list means a
  service without redis installed loses httpx and psycopg tracing too — silently.
  The flight service is exactly that case.
- **`/metrics` is a route, not a mount.** `app.mount("/metrics", ...)` answers
  the un-slashed path with a 307, so every scrape — once every five seconds,
  forever — would pay for a redirect before getting any data.
- **`/health` and `/metrics` are excluded from tracing.** They are polled
  constantly and would drown the traces of real work.
- **Export failures are survivable.** If Jaeger is down the SDK drops spans and
  the services keep serving. Observability that can take production down is worse
  than none.
