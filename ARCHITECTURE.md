# VoiceOps — how the whole thing fits together

A map of the repo. Every file gets a line or two in plain English, and the
**[flows at the end](#part-3--how-it-all-connects)** show how they work together.

Read the flows first if you want the shape; come back to the file list when you
need to know where something lives.

---

## Part 1 — The big picture

**What this is:** you speak into a browser, an AI answers you about flights, and
it can actually change your booking. When something breaks, the system retries by
itself instead of losing your request.

### The six pieces

| Piece | What it is | Where | Language |
|---|---|---|---|
| **Dashboard** | The web UI you log into | :3001 | Next.js + React |
| **Platform API** | The front door. Checks who you are, what you're allowed to do | :3000 | Node + Fastify |
| **AI service** | Speech → thinking → speech. Decides which tool to run | :8000 | Python + FastAPI |
| **Flight service** | A fake airline backend, with a "break it" switch | :8002 | Python + FastAPI |
| **Worker** | Does background work, retries what failed | no port | Python |
| **Datastores** | Postgres (memory) + Redis (the job queue) | :5432 / :6379 | — |

Plus three observability tools: **Jaeger** (:16686) shows a request's journey,
**Prometheus** (:9090) collects numbers, **Grafana** (:3002) draws them.

### Two rules that explain most decisions

**1. The AI service is never reachable from outside.** It can cancel bookings and
has no idea who is calling. Only the Platform API can reach it, and the API checks
your token first. This is why the dashboard talks to :3000 and never to :8000.

**2. Redis holds *work*, Postgres holds *history*.** Redis knows what still needs
doing right now. Postgres remembers everything that ever happened. Redis forgets a
finished job; Postgres keeps it so the dashboard can show you.

### Why the AI models run outside Docker

Docker on a Mac cannot use the GPU. Whisper (speech) and Ollama (the language
model) need it, so they run directly on your machine, and containers reach them
through `host.docker.internal`. That single limitation shapes a lot of the setup.

---

## Part 2 — Every file, explained

### Root

| File | What it does |
|---|---|
| `Makefile` | Every command you type. `make up`, `make test`, `make ai`. Run `make` alone to see the list. |
| `docker-compose.yml` | Defines the 8 containers: Postgres, Redis, flight service, and the admin/observability UIs. |
| `.env` / `.env.example` | All settings in one file — database URL, which AI provider to use, chaos knobs. Both Python and Node read it. |
| `ruff.toml` | Python linting rules, with a written reason for every rule that is switched off. |
| `PROGRESS.md` | The running diary. What was built each phase, what broke, and why each decision was made. |
| `overview.md` | The original vision document this project started from. |
| `README.md` | Quick start. |
| `ARCHITECTURE.md` | This file. |

### `db/migrations/` — the database shape

Plain SQL files, run in order. Never edited once applied (a checksum refuses).

| File | What it creates |
|---|---|
| `0001_init.sql` | The 12 enum types (call status, job status, priority…) and a helper that auto-updates `updated_at`. |
| `0002_customers_flights_bookings.sql` | Customers, flights, bookings — the airline data. |
| `0003_calls_conversations.sql` | Calls and every turn of conversation, including how long each stage took. |
| `0004_jobs_failures.sql` | Jobs, one row per *attempt*, and a failure log. This is what the retry history is built from. |
| `0005_users_tickets.sql` | Dashboard logins and support tickets. |
| `0006_reference_data.sql` | Real airports, airlines and routes downloaded from OpenFlights. |

### `packages/queue-py/` — the job queue (built by hand, not a library)

This is the heart of the project. It is written from raw Redis commands on purpose.

| File | What it does |
|---|---|
| `models.py` | The `Job` object. Also converts it to/from the flat strings Redis stores. |
| `keys.py` | The naming scheme for every Redis key, written down so a human can inspect the queue. |
| `client.py` | The **producer** side: add a job, check queue depth, requeue a dead one. |
| `consumer.py` | The **consumer** side: take a job, mark it done, retry it, or rescue one from a dead worker. The tricky parts are Lua scripts so Redis runs them atomically. |
| `backoff.py` | How long to wait before retrying. Doubles each time, with randomness so all jobs don't retry at once. |
| `errors.py` | Decides "is this worth retrying?" A 503 yes, a 404 never. |
| `propagation.py` | Carries the trace ID into a job so you can follow it later. Works fine if tracing isn't installed. |
| `tests/` | 37 tests. Pure logic tests need nothing; queue tests run against a real Redis. |

### `packages/telemetry-py/` — shared observability

| File | What it does |
|---|---|
| `tracing.py` | Sets up tracing once per service, and provides the `span()` helper used to time things. |
| `logs.py` | Makes every log line carry the trace ID, so a log and a trace can be joined. |
| `metrics.py` | Every metric in the system, declared in one place so names never drift. |
| `__init__.py` | `setup()` — one call that turns on tracing, logging and metrics together. |

### `services/ai/` — the voice brain

**Entry points**

| File | What it does |
|---|---|
| `app/main.py` | Starts the service. Turns on tracing, checks the flows are valid, opens the database. |
| `app/config.py` | Reads settings — which model to use, where the flight service is, how many tool loops to allow. |
| `app/api/routes.py` | The two endpoints: `/v1/turn` (text in, text out) and `/v1/turn/audio` (speech in, speech out). |

**Providers — swappable AI backends**

| File | What it does |
|---|---|
| `providers/base.py` | The contract. Any speech, language or voice provider must look like this. |
| `providers/registry.py` | The only file that knows which provider is actually selected. Swapping = changing an env var. |
| `providers/stt/local_whisper.py` | Speech → text using whisper.cpp on your Mac. |
| `providers/stt/groq.py` | Same job, using Groq's free hosted API. |
| `providers/llm/ollama.py` | The local language model (`qwen2.5:7b-instruct`) that picks tools. |
| `providers/llm/groq.py` / `gemini.py` | Hosted alternatives. |
| `providers/tts/piper.py` | Text → speech. |

**The orchestrator — where a turn actually happens**

| File | What it does |
|---|---|
| `orchestrator/turn.py` | The most important file in the service. Loops: ask the model → run the tool it wants → feed the result back → repeat until it answers. **Also holds the confirmation gate.** |
| `orchestrator/prompt.py` | The system prompt. Deliberately only 591 characters — a longer one broke tool-calling. |

**Tools — what the AI is allowed to do**

| File | What it does |
|---|---|
| `tools/base.py` | What a tool is: a name, a schema the model sees, and a result. Tools return failures, never throw. |
| `tools/flight.py` | The 9 real tools — check status, get booking, cancel, reschedule, refunds, weather, aircraft, escalate. Also cleans up misheard flight numbers ("AI 858" → "AI858"). |

**Flows — narrowing what the AI can do**

| File | What it does |
|---|---|
| `flows/schema.py` | What a flow is: states, and which tools each state allows. |
| `flows/loader.py` | Reads the YAML and caches it. |
| `flows/servicing.yaml` | The actual flow: `identify` → `servicing` → `choosing_flight` → done or escalated. |

**Checks**

| File | What it does |
|---|---|
| `eval_tools.py` | Measures how often the model picks the right tool. How prompt changes were proven, not guessed. |
| `test_voice_loop.py` | Speaks real audio at the agent, end to end. Needs the real model. |
| `tests/test_gate.py` | Proves the confirmation gate blocks unconfirmed booking changes. |
| `tests/test_tools.py` | Flight-number cleanup, tool descriptions, which tools are marked dangerous. |
| `tests/test_flows.py` | Every tool a flow names really exists; states really do narrow the tool list. |
| `tests/conftest.py` | The fake LLM used by tests, so nothing depends on a real model. |

### `services/flight-mock/` — the fake airline (and the chaos switch)

| File | What it does |
|---|---|
| `app/main.py` | Starts the service and installs the chaos middleware. |
| `app/chaos.py` | Breaks the service on demand — 7 scenarios: `healthy`, `flaky`, `hard_down`, `slow`, `rate_limited`, `timeouts`, `corrupt`. `/health` and `/admin` are never broken. |
| `app/errors.py` | Says clearly whether a failure is permanent or worth retrying. |
| `app/db.py` | Database access. |
| `app/config.py` | Settings. |
| `app/routers/flights.py` | Flight status lookup. |
| `app/routers/bookings.py` | Get, cancel and reschedule a booking, with the real business rules. |
| `app/routers/refunds.py` | Refund status. |
| `app/routers/live.py` | Real weather (NOAA) and real aircraft positions (OpenSky). |
| `app/routers/admin.py` | Turn chaos on and off at runtime. Immune to chaos itself. |
| `app/live.py` | Talks to NOAA and OpenSky, with caching so we don't hammer them. |
| `Dockerfile` | Builds the container. The only service that runs inside Docker. |

### `services/worker/` — background jobs

| File | What it does |
|---|---|
| `app/main.py` | Two loops. **Workers** take jobs and run them. **The scheduler** releases jobs whose time has come and rescues jobs from dead workers. |
| `app/handlers.py` | The actual work for each job type. Raises a failure with enough detail for the retry engine to judge it. |
| `app/mirror.py` | Copies every job, attempt and failure into Postgres so the dashboard can show history. Best-effort — never loses a job over a database hiccup. |
| `app/config.py` | Settings: how many workers, how long a job may be held, retry limits. |

### `services/api/` — the front door

| File | What it does |
|---|---|
| `src/index.ts` | Starts the process and listens. |
| `src/app.ts` | Builds the app — routes, auth, error handling. Separate so tests can drive it without opening a port. |
| `src/config.ts` | Reads the shared `.env`. |
| `src/telemetry.ts` | Tracing and metrics. Must be imported first or instrumentation silently does nothing. |
| `src/auth/rbac.ts` | The permission table. Who can do what, in one readable list. |
| `src/auth/plugin.ts` | Checks the JWT, and the `requires('jobs:read')` guard used on routes. |
| `src/lib/db.ts` | Postgres access. Always parameterised, never string-glued. |
| `src/lib/queue.ts` | Only the producer half of the queue — add, inspect, requeue. The hard parts stay in Python. |
| `src/lib/ids.ts` | Rejects a malformed `:id` with a 400 instead of letting Postgres throw a 500. |
| `src/routes/auth.ts` | Login, and "who am I". |
| `src/routes/calls.ts` | List calls, one call with its transcript, escalate a call. |
| `src/routes/jobs.ts` | Queue state, attempt history, requeue a dead job. |
| `src/routes/analytics.ts` | The numbers for the charts — all computed in SQL, not in Node. |
| `src/routes/voice.ts` | Passes your turn to the AI service. The only way in. |
| `src/auth/rbac.test.ts` | 52 tests: every role against every permission. |
| `src/app.integration.test.ts` | 15 tests driving real HTTP requests through the whole app. |

### `services/web/` — the dashboard

| File | What it does |
|---|---|
| `app/page.tsx` | Sends you to the login page or the calls page. |
| `app/layout.tsx` | The HTML shell. |
| `app/globals.css` | The whole design system — colours, the drifting background, every animation. |
| `app/login/page.tsx` | Sign in. Has one-click buttons for the seeded accounts. |
| `app/(dash)/layout.tsx` | The top bar. Hides nav tabs your role cannot use. |
| `app/(dash)/calls/page.tsx` | Call totals, latency by stage, intents, and the recent-calls table. |
| `app/(dash)/calls/[id]/page.tsx` | One call: full transcript, the tools it ran, and jobs it created. |
| `app/(dash)/queue/page.tsx` | Live queue. Refreshes every 3 seconds. Shows the retry backoff growing. |
| `app/(dash)/failures/page.tsx` | What is failing, split into "worth retrying" and "never worth retrying". |
| `app/(dash)/console/page.tsx` | Hold-to-talk. The live voice agent, with a microphone level meter. |
| `lib/api.ts` | Every call to the Platform API, in one file. |
| `lib/ui.tsx` | Shared pieces — status pills, cards, bars, the count-up animation. |

### `scripts/` — one-off tools

| File | What it does |
|---|---|
| `doctor.sh` | 25 checks: is everything installed, running and reachable? Run this first when confused. |
| `migrate.py` | Applies the SQL migrations. Refuses to run an already-applied file that was edited. |
| `seed.py` | Fills the database with realistic fake calls, bookings and jobs. Same data every time. |
| `load_reference.py` | Downloads real airports, airlines and routes. Cached. |
| `enqueue.py` | Add a job by hand, for testing. |
| `queue_status.py` | Print queue depth and dead letters. `make queue W=1` to watch live. |
| `verify_trace.py` | Sends a real turn and proves it shows up as one trace across all services. |
| `metrics.sh` | Shows what each service is currently exposing to Prometheus. |
| `test.sh` | Runs all six test suites and prints one summary. |

### `infra/` and `.github/`

| File | What it does |
|---|---|
| `infra/observability/prometheus.yml` | Which services to collect numbers from. |
| `infra/observability/grafana/dashboards/overview.json` | The 10-panel dashboard, stored in git so it survives a reset. |
| `infra/observability/grafana/provisioning/` | Wires Grafana to Prometheus and Jaeger automatically. |
| `.github/workflows/ci.yml` | On every push: lint, unit tests, integration tests, Docker build. |
| `.github/workflows/nightly-eval.yml` | Nightly: measures real tool-selection accuracy. Never blocks a merge. |
| `tests/load/voice.js` | k6 load test. Not run in CI — a CI runner has no AI model. |

### `docs/`

| File | Covers |
|---|---|
| `schema.md` | The database tables. |
| `queue-protocol.md` | Exactly how the Redis queue works. |
| `voice.md` | The speech pipeline. |
| `flows.md` | How flows narrow the tool set. |
| `chaos.md` | The 7 ways to break the flight service. |
| `api.md` | Every API endpoint. |
| `dashboard.md` | The screens and the design system. |
| `real-data.md` | Where the real airport/weather/aircraft data comes from. |
| `observability.md` | Traces, metrics, logs. |
| `testing.md` | The test suites and CI. |
| `decisions/0001…0006` | **Six short notes explaining the six least obvious choices.** Worth reading — each says what was decided, why, and what it cost. |

---

## Part 3 — How it all connects

Six flows. Follow the arrows.

### Flow 1 — You ask a question

> *"Is flight 6E597 delayed?"*

```
  You (browser)
      │  hold the button, speak
      ▼
  Console page  ─────────────────────── services/web/app/(dash)/console/page.tsx
      │  records audio, POSTs it
      ▼
  Platform API  :3000 ───────────────── src/routes/voice.ts
      │  1. is your token valid?          src/auth/plugin.ts
      │  2. do you have 'voice:use'?      src/auth/rbac.ts
      │  3. pass it on
      ▼
  AI service  :8000 ────────────────── app/api/routes.py
      │
      ├─ 1. SPEECH → TEXT              providers/stt/local_whisper.py
      │     whisper.cpp on your Mac        ~500ms
      │     "is flight 6E597 delayed"
      │
      ├─ 2. WHICH TOOLS ARE ALLOWED?   flows/servicing.yaml
      │     state = 'identify' → 5 tools, not 9
      │
      ├─ 3. ASK THE MODEL              orchestrator/turn.py
      │     Ollama qwen2.5 ─────────────► "call check_flight_status"
      │                                     ~5s  ← the slow part
      │
      ├─ 4. RUN THE TOOL               tools/flight.py
      │     │                             cleans "6E 597" → "6E597"
      │     └──► Flight service :8002 ──► Postgres
      │            ◄── {status: ON_TIME}    ~30ms
      │
      ├─ 5. ASK THE MODEL AGAIN        with the tool result added
      │     ─────────────────────────────► "Flight 6E597 is on time…"
      │
      ├─ 6. TEXT → SPEECH              providers/tts/piper.py
      │
      └─ 7. SAVE EVERYTHING            core/persistence.py → Postgres
                                          both turns, timings, tools used
      │
      ▼
  You hear the answer.
```

**The one number worth remembering:** of ~10 seconds, about 9.9 is the model
thinking. The flight lookup is 30ms. If you ever want this faster, the model is
the only place that matters.

---

### Flow 2 — You cancel a booking (the safety gate)

This is the most important logic in the project. It exists because an early
version cancelled a **real booking** without ever asking.

```
  TURN 1 — "What's my booking UYO57P?"

      model → get_booking(UYO57P)
      tool succeeds ✓
      ────────────────────────────────────
      remember: UYO57P has been READ BACK
      ────────────────────────────────────
      agent: "It's SG384, Delhi to Chennai, 23:16…"


  TURN 2 — "Yes, cancel it"

      model → cancel_booking(UYO57P)
             │
             ▼
      ┌──────────────────────────────────────────────┐
      │  THE GATE   orchestrator/turn.py             │
      │                                              │
      │  Was this booking read back on an            │
      │  EARLIER turn?                    ✓ yes      │
      │                                              │
      │  Has any other tool already run              │
      │  THIS turn?                       ✓ no       │
      │                                              │
      │  Both true → allowed                         │
      └──────────────────────────────────────────────┘
             │
             ▼
      booking cancelled.
```

**Why both checks?** Each alone has a hole:

| Missing check | What goes wrong |
|---|---|
| No "read back earlier" | It cancels a booking you were never told about |
| No "nothing ran this turn" | It looks up *and* cancels in one breath — the original bug |

The gate is **code, not a prompt instruction**. A model can be talked out of an
instruction. It cannot be talked out of an `if` statement.

Try it yourself: say *"cancel booking UYO57P"* as your very first message. It
will refuse and describe the booking first.

---

### Flow 3 — The flight service is down

Say the cancellation was confirmed, but the airline API is unreachable. You
already said yes. Losing your request would be the worst possible outcome.

```
  cancel_booking → Flight service → 503 Service Unavailable
                                       │
                                       ▼
                    Is this worth retrying?    errors.py
                    503 → YES (transient)
                                       │
                                       ▼
                    ┌────────────────────────────────┐
                    │  PUT IT ON THE QUEUE           │
                    │  core/queue.py → Redis         │
                    └────────────────────────────────┘
                                       │
      agent tells you: "That's queued — you don't need to call back."
      ↑ the call ENDS here. The rest happens without you.

  ─────────────────────────────────────────────────────────────

  WORKER  services/worker/app/main.py

   attempt 1 ──► 503 ✗   wait 1.9s
   attempt 2 ──► 503 ✗   wait 3.2s      ← each wait roughly doubles,
   attempt 3 ──► 503 ✗   wait 5.4s        with randomness added
   attempt 4 ──► 503 ✗   wait 14.7s
   attempt 5 ──► 503 ✗
                  │
                  ▼
            DEAD LETTER — automation gives up, a human is needed
                  │
                  │   you fix the cause, then click "Requeue"
                  ▼      on the dashboard queue page
            attempt 1 ──► 200 OK ✓   booking cancelled
```

**Why the waits grow:** if every failed job retried instantly, they'd all hammer
the recovering service at once and knock it over again. The randomness stops them
retrying in lockstep.

**A permanent error skips all of this.** A 404 "no such booking" fails on attempt
1 and is never retried — asking again cannot change the answer. That single
distinction is what `errors.py` exists for.

---

### Flow 4 — A worker dies mid-job

```
  worker takes a job
      │
      │  Redis: move job OUT of the waiting list
      │         INTO the "in progress" set, stamped with a deadline
      ▼
  worker starts working…
      │
      💀 kill -9    (crash, deploy, laptop sleeps)
         no "done", no "failed" — just silence
      │
      ▼
  60 seconds pass, the deadline expires
      │
      ▼
  SCHEDULER notices     services/worker/app/main.py
      │  "this job's lease expired — its worker is gone"
      ▼
  job goes back to the waiting list, attempt count +1
      │
      ▼
  another worker picks it up. Nothing was lost.
```

The attempt count matters: without it, a job that reliably crashes its worker
would be retried forever.

---

### Flow 5 — Following one request across everything

Every request gets an ID. That ID travels with it everywhere.

```
  Platform API
     │  generates trace id: a1b2c3…
     │  puts it in the HTTP header ──────────┐
     ▼                                        │
  AI service reads the header ◄───────────────┘
     │  now logs the SAME id
     │
     ├── span: llm.complete        6.7s
     ├── span: tool check_flight   0.07s
     │      └── Flight service reads the header too
     │
     └── queues a job?
            │  writes the trace id INTO the job   propagation.py
            ▼
         Redis  ──── 30 seconds later ────►  Worker
                                               │  reads it back out
                                               ▼
                                     attempt spans join the SAME trace
```

Open http://localhost:16686 and you see the whole story as one picture: the call,
the model, the tools, and every retry, nested under one another.

**Why this matters:** without it, a worker error at 02:35 and a slow call at 02:34
are two separate mysteries. With it, they're one clickable line.

`make test-trace` proves it in one command.

---

### Flow 6 — What `make up` actually does

```
  make up
    │
    ├─ make vendor        copy the telemetry package into the
    │                     flight service's build folder
    │                     (it can't reach outside its own folder)
    │
    └─ docker compose up
         │
         ├─ postgres        ──► waits until it answers
         ├─ redis           ──► waits until it answers
         ├─ flight-mock     ──► needs postgres + jaeger first
         ├─ jaeger          traces
         ├─ prometheus      numbers
         ├─ grafana         charts (already wired up)
         ├─ pgweb           browse the database
         └─ redisinsight    browse Redis

  Then, in separate terminals, on your Mac (not Docker — they need the GPU):

     make worker    the background job runner
     make ai        the voice brain
     make api       the front door
     make web       the dashboard  →  http://localhost:3001
```

---

## Where to start reading the code

If you want to actually understand it, read in this order:

| # | File | Why |
|---|---|---|
| 1 | `services/ai/app/orchestrator/turn.py` | The heart. One conversation turn, start to finish, including the safety gate. |
| 2 | `packages/queue-py/voiceops_queue/consumer.py` | The hardest code here. Atomic reservation, leases, crash recovery. |
| 3 | `services/ai/app/tools/flight.py` | What the AI can actually do, and how failures are handled. |
| 4 | `services/api/src/auth/rbac.ts` | Tiny, and the whole permission model is visible at once. |
| 5 | `docs/decisions/` | Six short notes on the six least obvious choices. |

Then `PROGRESS.md` for the story of how it got built — including the bugs, which
is where most of the real learning is.
