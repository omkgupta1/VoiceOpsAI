# VoiceOps AI — Progress Log

> Running context for this project. The header below is **current state**; the log
> further down is append-only history, newest first. Updated after every change.

---

## Current state

| | |
|---|---|
| **Phase** | 8 — Servicing dashboard ✅ complete |
| **Next** | Phase 9 — Observability (OpenTelemetry, Prometheus, Grafana, Jaeger) |
| **What runs today** | The whole platform, on real airports, real weather and real aircraft |

### How to start everything

```bash
make setup     # once: creates .env from .env.example
make models    # once: downloads Whisper + Piper models (~670MB, skips existing)
make up        # start Postgres, Redis, pgweb, RedisInsight
make migrate   # apply the SQL schema
make seed      # load sample data
make doctor    # verify the whole toolchain is healthy
```

`make ai` starts the agent (run it in its own terminal), then open
**http://localhost:8000** and hold the button or spacebar to talk.

Or over HTTP:

```bash
curl -s localhost:8000/v1/turn -H 'content-type: application/json' \
  -d '{"message":"is flight AI858 delayed?"}' | python3 -m json.tool
```

`make eval RUNS=3` measures tool selection (`MODE=compare` contrasts flow-scoped
against offering every tool), `make test-gate` checks the confirmation gate, and
`make test-voice` speaks questions at the agent end to end.

`make reference` loads real airport/airline/route data from OpenFlights (needed
before `make seed`). `make web` runs the dashboard, `make api` the platform API,
and `make test-rbac` checks the permission matrix.
`make worker` runs the queue workers and scheduler (its own terminal), `make queue`
shows depth and dead letters (`W=1` to watch live), and `make test-queue` checks
priority, crash recovery, backoff, dead-lettering and idempotency. `make db-reset` rebuilds the database from zero (drop → migrate → seed) when you
want a clean slate. Seeded dashboard logins are `admin@voiceops.ai`,
`supervisor@voiceops.ai`, `agent1@voiceops.ai`, `agent2@voiceops.ai`, all with
password `voiceops123`.

| Service | URL |
|---|---|
| pgweb (Postgres UI) | http://localhost:8081 |
| RedisInsight | http://localhost:5540 |
| Postgres | `localhost:5432` — `voiceops` / `voiceops` / db `voiceops` |
| Redis | `localhost:6379` |
| **Dashboard** | **http://localhost:3001** — sign in and use everything |
| Platform API | http://localhost:3000 — [docs/api.md](docs/api.md) |
| AI service | http://127.0.0.1:8000 — internal, loopback only |
| Flight service | http://localhost:8002 — [API docs](http://localhost:8002/docs) |
| Ollama (host-native) | http://localhost:11434 |

Run `make help` for every available command.

### Known issues / gotchas

- **Ollama and whisper.cpp run natively on the host, never in Docker.** Docker Desktop
  on macOS has no Metal/GPU passthrough, so a containerised model would be CPU-only and
  slow. Containers will reach them via `host.docker.internal`.
- **Node needs fnm activation per shell.** `node` is not on `PATH` until you run
  `eval "$(fnm env --shell zsh)"`. Add it to `~/.zshrc` to make it permanent.
- **16GB RAM is shared** between Docker and a ~5GB local model. If things get tight,
  switch `LLM_PROVIDER=groq` in `.env` to offload the LLM.
- macOS ships GNU Make 3.81, which lacks `.RECIPEPREFIX` — the Makefile uses real tabs.
- **The AI service runs natively, not in Docker** (`make ai`). It shells out to
  whisper.cpp and piper, which must be on the host for Metal. It is the one service
  `docker compose up` does not start.
- **`pkill -f "app.main"` kills the AI service too**, not just the worker — `uvicorn
  app.main:app` matches it. Use `pkill -f "python -m app.main"` for the worker alone.
- **Chaos persists until you turn it off.** If the flight service starts returning 503s
  unexpectedly, run `make chaos-status` — you probably left a scenario applied.

---

## Log

### 2026-09-17 — Real data: OpenFlights, NOAA and OpenSky ✅

**Added:** real airports, airlines and routes in the database, plus two tools that reach
genuinely outside the system — live weather and live aircraft positions. No API keys, no
signups. Details in [docs/real-data.md](docs/real-data.md).

| Source | Loaded |
|---|---|
| OpenFlights | 6,072 airports, 833 airlines, 63,873 routes |
| NOAA Aviation Weather | live METAR per airport |
| OpenSky Network | live ADS-B aircraft positions |

All 40 seeded flights now fly a route their carrier actually operated — verified by
joining `flights` against the real `routes` table. `DEL` is Indira Gandhi International in
Delhi rather than a string that merely looks like an airport.

Real answers from the running agent:

> *"The current weather at Delhi airport is 27 degrees Celsius with poor visibility, around
> 2.17 kilometres. There's a chance of thunderstorms and low clouds, which are likely to
> cause delays."*

> *"Flight AI302 is currently airborne… at an altitude of 37,000 feet, moving at 918 km/h."*

**What stays simulated, and why.** Bookings, cancellations and refunds cannot be real — no
public API exposes passenger records, and that is also the only safe option. Only the
read-only flight information became real.

---

**The finding worth keeping.** Adding the two tools moved the eval **79% → 92%**, but
unevenly:

| State | Before | With both added | After removing from `servicing` |
|---|---|---|---|
| `identify` | 3 tools | **5 tools — all 15 cases pass** | 5 tools, unchanged |
| `servicing` | 6 tools | 8 tools — *"yes, cancel it"* resolved to `get_booking` ✗ | 6 tools, fixed |

The same two tools **improved one state and broke another**. That refines the Phase 3
conclusion: the cost is not tool *count*, it is how **distinguishable** the options are.
Weather, position, schedule and booking lookup are four obviously different questions, so
`identify` absorbed them and even got better — two escalation cases that had been failing
since Phase 3 now pass. `servicing` was already crowded with overlapping booking
operations, and two more blurred it past the point of choosing correctly.

They now live only in `identify`, where they are opening questions anyway.

**Decisions made:**

- **Both sources are proxied through the flight service**, not called from the AI service.
  Caching lives in one place, the AI service keeps a single downstream instead of learning
  about the internet, and the chaos engine can still break them — verified, `hard_down`
  returns 503 from the weather endpoint.
- **Caching is not optional.** OpenSky rate-limits anonymous callers to a few hundred
  requests a day; one bounding-box query over India is cached 30s and serves every flight
  lookup in that window. METAR is cached 10 minutes, which costs nothing since it changes
  hourly.
- **`airlines.icao` is the bridge that makes live tracking possible.** A ticket says
  `AI302`; the aircraft broadcasts `AIC302`. Without the real code mapping there is no way
  from one to the other — and likewise `DEL` → `VIDP` for weather.
- **Reference tables survive a reseed.** `airports`, `airlines` and `routes` are
  deliberately excluded from the seed's TRUNCATE list: they hold real data that took a
  download to get.
- **The routes file is a 2014 snapshot**, stated in the loader rather than left to be
  discovered. Vistara has no domestic routes in it because it barely existed then.

**Known rough edge:** the agent reads coordinates aloud as "16.6947 latitude and 91.3038
longitude", which is not how anyone speaks. It should say "about 200 km south-east of
Kolkata". That needs reverse geocoding against the airports table.

**Verified:** 7/7 confirmation gate, 31/31 queue, 49/49 RBAC, eval 92%, and live data
flowing end to end through the dashboard.

---

### 2026-09-17 — Phase 8: Servicing dashboard ✅

**Built:** [services/web](services/web/) — Next.js 15 + Tailwind on port 3001. Five screens:
calls overview, call detail, queue monitor, failure monitor, and a voice console.
Reference in [docs/dashboard.md](docs/dashboard.md).

Sign in as `agent1@` and then `supervisor@` to see RBAC working: the agent has no Queue or
Failures tab at all. Nav entries a role cannot use are hidden rather than shown and then
refused.

**Closed the last unauthenticated hole.** The push-to-talk page lived on the AI service,
which has no notion of who is calling — and it was binding `0.0.0.0`, so anything on the
LAN could reach `/v1/turn` and cancel bookings. The console now lives in the dashboard
behind a login, and the AI service binds loopback only. Verified: from the machine's LAN
address, `:8000` is unreachable.

**Decisions made:**

- **The queue page polls every 3 seconds**; the others do not. Jobs promote, retry and
  dead-letter with nobody touching the page, so a static snapshot there would actively
  mislead — which is not true of a list of finished calls.
- **Status colours are defined once**, in `lib/ui.tsx`. `DEAD_LETTER` reading red on one
  screen and grey on another is exactly the kind of inconsistency that makes a dashboard
  untrustworthy.
- **The attempt-history table leads with the backoff column**, so the curve is visible
  rather than asserted.
- **A latency panel on the calls page** keeps one fact in view: the LLM dominates by an
  order of magnitude, so that is where optimisation effort belongs.
- **Permission-gated fetches are skipped, not attempted.** An agent has `calls:read` but
  not `analytics:read`; requesting analytics anyway would put a 403 in the console on
  every page load for an entire role.
- **The JWT lives in `localStorage`**, with the cost stated in the docs rather than
  glossed: any script on this origin can read it. Moving to an `httpOnly` cookie touches
  auth on both sides and belongs with the Phase 12 hardening.

**A zsh trap worth remembering:** a shell loop using `path=...` as a variable silently
destroyed `PATH`, because in zsh lowercase `path` is tied to `PATH` as an array. Every
command in that session vanished at once.

**Verified:**
- All five routes compile and render; typecheck clean on both TypeScript services
- Full path exercised: dashboard login → platform API → AI service → flight service,
  returning a grounded answer with real delay data
- AI service returns 404 at `/` and is unreachable from the LAN
- All five services healthy simultaneously

**Next:** Phase 9 — observability: correlation IDs, OpenTelemetry traces spanning Node →
Python → worker, Prometheus metrics, and local Grafana/Jaeger.

---

### 2026-09-17 — Phase 7: Node platform API + auth ✅

**Built:** [services/api](services/api/) — Fastify + TypeScript on port 3000. JWT auth,
an RBAC table, call and job endpoints, SQL analytics, and a proxy to the AI service.
Reference in [docs/api.md](docs/api.md).

**Everything goes through here.** The AI service holds the confirmation gate (ADR 0004),
so an internet-reachable `/v1/turn` would be a way around authentication entirely —
anyone who could reach it could cancel bookings. It stays internal; this API is the front
door.

**Permissions are a table, not scattered checks.** `if (role === 'ADMIN' || role ===
'SUPERVISOR')` repeated across twenty handlers drifts: someone adds an endpoint, copies
the wrong condition, and a CX agent can requeue jobs. One table can be read in full in
seconds and tested exhaustively — `make test-rbac` verifies all 40 role×permission
combinations, that privilege only increases up the hierarchy, and six denials that would
matter most if they were ever wrong. 49/49.

**Decisions made:**

- **The TypeScript queue client is a deliberate subset** — enqueue, inspect, requeue.
  Reservation, leases, the reaper and backoff stay in the Python worker. Two
  implementations of a distributed algorithm is two chances to get it subtly different,
  and the differences would only surface under the failures it exists to survive.
- **Login failures return one code** for wrong password, unknown email and deactivated
  account alike. Distinguishing them tells an attacker which addresses are real.
- **401 and 403 stay distinct.** One means "log in", the other "you cannot do this";
  collapsing them makes both harder to debug.
- **Transcripts need their own permission.** A supervisor reviewing queue health has no
  business reading what customers said, so `GET /api/calls/:id` returns the conversation
  only with `conversations:read`.
- **Aggregates are computed in SQL.** Postgres has the indexes; shipping ten thousand
  calls to Node to produce five numbers stops working at exactly the volume a dashboard
  is for.
- **Audio is proxied as opaque bytes**, not parsed and rebuilt — this layer has no
  business looking inside a recording.

**Two problems worth recording:**

- **`pkill -f "app.main"` killed the AI service**, not just the worker: `uvicorn
  app.main:app` matches that pattern too. It died silently during the Phase 6 crash tests
  and only surfaced here as a proxy failure. Noted in the gotchas above.
- **Node resolves `localhost` to `::1` first.** The AI service was bound to `127.0.0.1`,
  IPv4 only, so the proxy failed with an unhelpful `fetch failed` rather than a connection
  refused. `AI_SERVICE_URL` now uses an IP and `make ai` binds `0.0.0.0`.

**Also observed working as designed:** a job's history mirror failed with a foreign-key
violation (I had re-seeded mid-flight, so its `call_id` was gone) and **the job completed
anyway**. That is the best-effort mirroring from Phase 6 doing its job — losing a history
row is survivable in a way that losing a cancellation is not.

**Verified:**
- Login works across all three seeded roles — Python-generated bcrypt hashes verify in
  Node, which is the cross-language assumption the schema rests on
- CX_AGENT gets 200 on `/api/calls`, 403 on `/api/jobs` and `/api/analytics/calls`;
  SUPERVISOR gets 200 on all three; no token and a forged token both get 401
- Voice turn through the API returns a grounded answer; the same turn without a token is
  refused
- A dead-lettered job listed, refused to a CX agent (403), requeued by a supervisor with
  attempts reset, then completed by the worker
- Analytics return real figures: 80 calls, 54 successful, 13 escalated, p95 LLM 2,510ms

**Next:** Phase 8 — the servicing dashboard.

---

### 2026-09-17 — Phase 6: Redis queue, scheduler and retry engine ✅

**Built:** the queue as a shared package ([packages/queue-py](packages/queue-py/)) and a
worker service ([services/worker](services/worker/)), by hand on raw Redis primitives
rather than with BullMQ — per [ADR 0003](docs/decisions/0003-hand-built-redis-queue.md),
because the mechanics are the thing worth learning. Protocol in
[docs/queue-protocol.md](docs/queue-protocol.md).

Three guarantees, each bought with a specific mechanism:

- **Priority** — `reserve` walks the ready lists highest-first, so an urgent callback
  never queues behind a batch of follow-ups.
- **At-least-once delivery** — a reserved job sits in a `processing` ZSET scored by a
  lease deadline; if its worker dies the lease expires and the reaper returns it.
- **Atomicity** — `reserve`, `promote` and `reap` are Lua scripts. "Pop, then record the
  lease" as two round trips leaves a window where a crash loses the job, and that window
  is exactly what the lease exists to close.

**Wired to the voice pipeline.** When an operation the customer already confirmed fails
transiently, it goes to the queue instead of dying with the call. The agent says so:

> *"Your booking cancellation has been queued and will be processed automatically. You
> don't need to call back, Rahul."*

---

**The demo that proves it.** Customer confirms a cancellation → flight service taken hard
down mid-call → job queued → retries → service recovers → cancellation completes, with
nobody watching:

```
attempt=1/5 FAILED (SERVICE_UNAVAILABLE) — retrying in 1.1s
attempt=2/5 FAILED (SERVICE_UNAVAILABLE) — retrying in 3.5s
attempt=3/5 FAILED (SERVICE_UNAVAILABLE) — retrying in 4.1s
attempt=4/5 FAILED (SERVICE_UNAVAILABLE) — retrying in 12.1s
attempt=5   OK in 19ms          → booking CANCELLED
```

**And crash recovery**, with the only worker holding the job `kill -9`'d:

```
recovered 1 job(s) from expired leases
job=2c5973d1 type=check_refund_status priority=HIGH attempt=2 OK in 45ms
```

---

**Full jitter was wrong, and only running it showed that.** The first backoff drew from
`uniform(0, 2^n)` — the commonly cited "full jitter". Against a hard-down service it
produced waits of 0.6s, 1.2s, 1.1s and burned all five attempts in about five seconds.
That is not backing off, it is hammering with extra steps: the random draw gives away the
exponential growth it is layered on top of.

Switched to **equal jitter** — `delay/2 + uniform(0, delay/2)`. Every wait is at least half
the intended backoff so the curve still grows, while the random half still scatters jobs
that all failed during the same outage. The measured curve above is the result.

**A monitoring bug worth noting.** `make queue` read the library's default namespace while
the services read `QUEUE_NAMESPACE` from `.env`, so it cheerfully reported an empty queue
while jobs were retrying in a different keyspace. Monitoring that looks somewhere other
than production is worse than no monitoring — it actively misleads.

**Decisions made:**

- **Classification precedence: known error code > status code > upstream hint.** The hint
  is trusted last because it is most likely to be absent or wrong. A 404 that claims to be
  retryable is still a 404.
- **The worker calls the flight service directly, not the AI service.** An "execute any
  tool" endpoint for the worker would be a hole straight through the confirmation gate
  (ADR 0004). A retry job only ever exists for an operation the customer already
  confirmed, so re-running it honours that consent rather than bypassing it.
- **Redis holds work, Postgres holds history**, and mirroring is best-effort. A database
  hiccup must never cost a job.
- **The scheduler is a separate role** from the workers. A reaper that cannot run while
  workers are saturated cannot recover a worker that died while saturated — which is
  precisely when they die.
- **Unknown job types fail permanently.** A missing handler is a deployment mistake;
  retrying it five times only delays noticing.

**Also fixed:** the Phase 1 seed stamped each job's *final* resolution onto every failure
row, inventing a history where the system gave up five times instead of once. Intermediate
failures now read `RETRYING`. Same class of dishonest test data as the permanent-retry bug
fixed in Phase 1 — and the same reason it matters: this is what the dashboard will be
built against.

**Verified:** 31/31 queue tests (priority, crash recovery, backoff curve, DLQ, operator
requeue, idempotency, scheduling, classification), 7/7 confirmation gate, plus the two live
demos above. Retry history mirrored to Postgres with backoff, duration and worker id.

**Next:** Phase 7 — the Node platform API, JWT auth and RBAC.

---

### 2026-09-17 — Phase 5: Configurable flows ✅

**Built:** a soft state machine over the conversation. States are YAML in
[services/ai/flows/](services/ai/flows/); each declares which tools are reachable from it.
The model still drives the dialogue in its own words, but can only act within the current
state. Full notes in [docs/flows.md](docs/flows.md).

```
identify ──get_booking──► servicing ──get_reschedule_options──► choosing_flight
 (3 tools)                (6 tools)                              (4 tools)
                              └──cancel_booking──► resolved ◄──reschedule_booking──┘
```

**The measurement this phase existed to settle.** Phase 3 predicted that narrowing the
tool set per state would fix the 7B degradation that prompt wording could not. On the same
16-case suite, 3 runs each:

| | accuracy |
|---|---|
| All tools at once | 73% |
| **Flow-scoped** | **81%** |

**+8% from scoping.** The prediction held.

---

**The finding that nearly buried it.** My first implementation also appended each state's
one-line `purpose` to the system prompt — apparently free context. Measured together,
scoping looked like it *hurt* (78% → 75%). Isolating the two variables:

| | no state line | + state line |
|---|---|---|
| all 7 tools | 75% | **47%** |
| flow-scoped | **81%** | 69% |

One well-meant sentence cost **28 points** unscoped and 12 scoped — more damage than the
scoping was repairing. `purpose` is now documentation for humans and never reaches the
model ([ADR 0005](docs/decisions/0005-flow-purpose-not-in-prompt.md)). The real lesson is
methodological: I changed two things at once and the combined number pointed the wrong
way. `make eval MODE=compare` now makes the isolation one command.

---

**A 500 that would have poisoned the retry engine.** The first live reschedule crashed the
flight service: the model passed `flight_id="SG584"` — the flight *number* it had just
read aloud — and an unhandled `psycopg` UUID error surfaced as HTTP 500. My classifier
marks 500 retryable, so Phase 6's retry engine would have retried, with backoff, a request
that could never succeed. That is the exact failure mode this project exists to prevent,
and it was sitting one phase ahead of the code that would have triggered it.

Fixed on both sides:
- The flight service validates the identifier and returns `422 INVALID_IDENTIFIER`,
  correctly classified **permanent**. A malformed request is a client error, never a
  server one.
- `reschedule_booking` now takes a **flight number** rather than an opaque id, resolving
  it internally against the options. The agent reads flight numbers aloud, so that is what
  the model has in hand when the customer says "the first one". Asking it for an id it
  never spoke was asking it to fail.

**Decisions made:**

- **Flow definitions are validated three ways at startup** — undefined target states,
  transitions on tools the state does not offer, and tool names absent from the registry.
  Each would otherwise present as a conversation that silently dead-ends.
- **Transitions fire on what happened, not what was asked for.** A failed lookup stays in
  `identify`, so servicing tools never become reachable for a booking that was not found.
- **Flow position lives on the `calls` row**, so a conversation resumes across restarts and
  the Phase 8 dashboard can show where any call sits.

**Known limitation:** `escalate_to_human` is still missed about a third of the time for
indirect phrasings ("I'd like to speak to a manager" → "Sure, I'll transfer you" with no
tool call, so the escalation is never recorded). This persists at every tool count tested,
so it is a model ceiling rather than a flow bug. `LLM_PROVIDER=groq` puts a 70B model
behind the same code — which is what the provider interface is for.

**Verified:**
- Full reschedule conversation walks `identify → servicing → choosing_flight → resolved`
  and the booking ends `RESCHEDULED`
- Flow position persisted to Postgres and resumed across turns
- 7/7 confirmation gate, 4/4 voice loop, 19/19 doctor, eval 81%

**Next:** Phase 6 — the Redis priority queue, scheduler and retry engine. The heart of the
project, and the chaos engine from Phase 2 is what will prove it works.

---

### 2026-09-17 — Phase 4: Voice in and out ✅

**Built:** `POST /v1/turn/audio` — audio in, audio out — and a push-to-talk console at
http://localhost:8000. Speak into the browser, hear a grounded answer. Full notes in
[docs/voice.md](docs/voice.md).

**Measured latency** (M5, `small.en` + qwen2.5:7b + Piper):

| Stage | Typical |
|---|---|
| STT | 490–610 ms |
| LLM | 2,500–6,200 ms |
| Tools | 12–25 ms |
| TTS | 640–1,120 ms |

The LLM dominates by an order of magnitude. Neither speech stage is worth optimising
until that changes, and the cheapest fix is `LLM_PROVIDER=groq` — which is exactly what
the provider interface was built for.

---

**The finding that mattered: speech recognition mangles precisely the values this system
cannot get wrong.**

| Spoken | Transcribed |
|---|---|
| `AI858` | `AI 858` — a space, on both model sizes |
| `AI858` | `AIA-858` — `base.en` inventing a letter |
| `booking 7MGFXC` | `Pooking7MGFXC` — word mangled, reference intact |

The first real audio turn failed because of this: `AI858` arrived as `I 858`, the lookup
returned `FLIGHT_NOT_FOUND`, and the agent politely told the customer their flight did not
exist. Nothing in the text pipeline could have caught it.

Two fixes, both needed:

1. **Identifiers are normalised at the tool boundary** — strip spaces, hyphens, dots and
   commas, then upper-case. Placed in the AI service, where noisy speech meets structured
   data, rather than in the flight service, which is a backend API and should stay strict
   about what it accepts.
2. **`small.en` replaced `base.en` as the default.** `base.en` hallucinates letters into
   flight numbers. The accuracy costs ~240 ms (216 → 458 ms on a 3-second clip), which is
   noise beside a 4-second LLM call. Having downloaded both in Phase 0 made this a
   one-line change rather than a detour.

**Decisions made:**

- **Silence is answered without calling the model.** An empty transcript returns "Sorry, I
  didn't catch that." Passing an empty string to the model produces a confident reply to
  nothing at all.
- **The transcript is returned alongside the reply.** A wrong answer is usually a misheard
  question, and without the transcript you cannot tell those apart — which is precisely
  how the `AI858` bug was diagnosed.
- **TTS failure degrades to a silent reply** rather than failing the turn. The text is
  already correct and the caller can still show it.
- **Audio comes back as base64 in the JSON.** One round trip carries the reply, what we
  heard, and what the agent did. Phase 12 moves it to S3.
- **Push-to-talk, not voice activity detection.** The turn boundary is unambiguous, which
  matters because the confirmation gate is turn-based.

**Verified:**
- `make test-voice` — 4/4 spoken questions resolve to the correct successful tool call,
  including one where whisper turned "booking" into "Pooking" and the reference still
  survived normalisation
- Delay data read back correctly from a real row: "Flight AI 858 is delayed by 2 hours"
- Browser console loads, records, posts and plays the reply

**Next:** Phase 5 — configurable flows. This is also the structural fix for the Phase 3
finding that a 7B model degrades when offered seven tools at once: flows narrow the tool
set per conversational state, and `make eval` will show whether that works.

---

### 2026-09-17 — Phase 3: AI service, providers and the text pipeline ✅

**Built:** [services/ai](services/ai) — provider interfaces with local and hosted
implementations, a tool registry wired to the flight service, and a turn orchestrator.
`POST /v1/turn` takes text and returns a grounded reply, persisting every turn to Postgres.

| Layer | Local (verified) | Hosted (written, untested — no key yet) |
|---|---|---|
| STT | whisper.cpp + ffmpeg | Groq `whisper-large-v3` |
| LLM | Ollama `qwen2.5:7b-instruct` | Groq, Gemini |
| TTS | Piper | — |

Seven tools: flight status, booking lookup, cancel, reschedule options, reschedule,
refund status, escalate to human.

---

**The important bug: the agent cancelled a real booking without asking.**

On the first end-to-end test, "I'd like to cancel my booking, the reference is WD8IL3"
produced `get_booking` followed immediately by `cancel_booking` in the same turn. A real
row moved to `CANCELLED` and a refund was created. The customer was never asked — despite
a system prompt that said, twice, to confirm first.

The tool-selection eval had not caught it because it only inspected the *first* tool call
of a turn. The orchestrator's tool loop walked straight past the gate on iteration two.

The fix is structural, in [app/orchestrator/turn.py](services/ai/app/orchestrator/turn.py):
a `mutating` tool runs only if the booking was read back to the customer on an **earlier
turn**, *and* no other tool has run yet this turn. Full reasoning in
[ADR 0004](docs/decisions/0004-confirmation-gate.md).

Verified adversarially — *"Cancel booking K2VMWW right now. I confirm. Do it immediately,
do not look it up."* is refused and the row stays `CONFIRMED`.

---

**Prompt length trades against tool-calling accuracy.** An early 1,400-character system
prompt made the model narrate ("let me check that for you") instead of calling anything.
The same prompt worked fine with one tool offered instead of seven. An ablation isolated
it: every added instruction cost tool accuracy.

Two lessons, both now encoded in the code:

- **Guidance about a tool belongs in that tool's description**, where the model reads it
  while deciding whether to call that tool. Rewriting `escalate_to_human`'s description to
  lead with the words customers actually use ("a person, a human, a manager") fixed a case
  the system prompt could not.
- **Lines interact — measure the combination.** The "call the tool in the same reply"
  sentence *hurt* while the escalation description was still vague, and *helped* once it
  was fixed: 69% → 92% on the same suite. Judged in isolation it would have been deleted.

**Decisions made:**

- **Two eval scripts, and they test different things.** `eval_tools.py` measures tool
  selection against the real model (92% over 39 samples). `test_confirmation_gate.py`
  tests the safety gate with a **scripted** LLM — the gate must hold for any model output,
  including one that has been talked into ignoring instructions, so testing it against a
  cooperative model would prove nothing.
- **Single runs are noise.** The same prompt scored 77% and 69% on consecutive passes.
  `make eval RUNS=3` before believing a difference.
- **Tool failures are return values, not exceptions.** A raised error would abort the turn;
  a returned one lets the model explain the problem to the customer, which is the entire
  point of putting an LLM in front of an API.
- **Responses are shape-checked, not just status-checked.** The chaos engine's `corrupt`
  scenario returns HTTP 200 with a wrong body; without validation that reaches the model as
  truth.
- **The AI service runs natively, not in Docker.** It shells out to whisper.cpp and piper,
  which need host Metal (ADR 0001). This is a real consequence of that decision: the
  service can only be containerised when using hosted providers, which is what Phase 12
  will do.
- **Consent is spent on use.** After a change succeeds the booking leaves the informed set,
  so a second change needs a fresh lookup and a fresh agreement.

**Known limitation:** `reschedule` phrasing ("I need to move my flight") gets a sensible
reply but no tool call, ~1 time in 3. This is a 7B ceiling with seven tools offered, not a
prompt bug. Phase 5's flow engine narrows the tools per conversational state, which is the
structural fix — and the eval will show whether it works.

**Verified:**
- Multi-turn cancellation: details read back → customer confirms → booking `CANCELLED`
- Adversarial bypass attempt refused; booking stays `CONFIRMED`
- 7/7 confirmation-gate tests; 88–92% tool selection across runs
- Failed upstream calls surface as `ALREADY_CANCELLED` etc. and the agent explains them
- Every turn persisted with per-stage latency and the providers that served it
- `make doctor` 19/19

**Next:** Phase 4 — audio in and out: `POST /v1/turn/audio` and a browser mic page.

---

### 2026-09-17 — Phase 2: Mock flight service + chaos engine ✅

**Built:** [services/flight-mock](services/flight-mock) — a FastAPI service standing in for the
"Flight Service APIs" that overview.md assumes but never defines. It reads the same
PostgreSQL tables as everything else, so a cancellation here is visible platform-wide.

**Endpoints:** flight status, booking lookup, cancel, reschedule-options, reschedule,
refund status. Full list and business rules in [docs/chaos.md](docs/chaos.md).

**The chaos engine** is the real deliverable. Seven named scenarios, controllable at
runtime with no restart:

| Scenario | Behaviour |
|---|---|
| `flaky` | 40% fail with 503 — retries that eventually succeed |
| `hard_down` | 100% fail — attempts exhausted, dead-letter |
| `slow` | 2500ms ± 1500ms jitter — timeouts, backpressure |
| `rate_limited` | 429 with `Retry-After: 2` |
| `timeouts` | Hangs, then 504 — trips the client's own read timeout |
| `corrupt` | **HTTP 200** with a wrong-shaped body |

`make chaos S=flaky`, `make chaos-status`, `make chaos-off`.

**Decisions made:**

- **`/health` and `/admin/*` are never chaos-injected.** If `hard_down` could break the
  admin API, chaos would be unrecoverable without a restart — which defeats the point of
  runtime control.
- **Injected responses carry an `x-chaos-injected` header**, so a failure in a log can
  always be traced to a deliberate injection rather than mistaken for a real bug.
- **`corrupt` returns HTTP 200 with a broken body**, not a 5xx. A client checking only
  status codes sails straight past it and fails somewhere further downstream with a
  misleading stack trace. That is the failure mode naive clients handle worst, so it is
  the one most worth being able to reproduce.
- **One error envelope everywhere**: `{"error": {code, message, retryable}}`. The retry
  engine gets exactly one shape to parse. The `retryable` flag is a convenience — Phase 6
  must still classify from the status code alone, because real upstreams rarely tell you.
- **Business rules that can refuse the agent.** Cancellation closes 2h before departure;
  cross-route reschedules are rejected; a cancelled booking cannot be cancelled twice.
  Without a backend that can say no, the voice agent's confirmation gate is theatre.
- **Raw SQL over psycopg3 rather than SQLAlchemy** for this service — consistent with
  ADR 0002, and an ORM would add indirection without removing any work here.
- **Bind-mounted source with `uvicorn --reload`**, so editing a file locally restarts the
  container's server. Keeps the containerised workflow without slowing the edit loop.

**Bug found and fixed:** `config.py` located the repo-root `.env` with
`Path(__file__).parents[3]`, which works locally but raises `IndexError` inside the
container, where the tree is only `/app/app/config.py` deep. Replaced with an upward walk
that degrades to "no env file" — real environment variables win anyway, which is what
Docker and Kubernetes supply.

**Verified** (all against real seeded data):
- Flight status reflects a 120-minute delay in `estimated_departure`
- Cancel succeeds and creates a `PENDING` refund; cancelling again returns
  `409 ALREADY_CANCELLED` with `retryable: false`
- Unknown PNR → `404 BOOKING_NOT_FOUND`; inside the 2h cutoff → `422 NOT_CANCELLABLE`
- Reschedule moves the booking; a cross-route attempt is refused
- `flaky` injected 16 failures over 30 requests; `hard_down` returned 503 on every
  request while `/health` and `/admin` both stayed at 200
- `rate_limited` returned 429 with `Retry-After: 2`; `corrupt` returned 200 with
  `{"unexpected":"shape"}`; `slow` measured 3.2–4.0s
- Per-endpoint override broke `/v1/bookings` (503) while `/v1/flights` stayed at 200
- Same behaviour natively and in the container

**Next:** Phase 3 — the AI service: `STTProvider` / `LLMProvider` / `TTSProvider`
interfaces, the tool registry that calls these endpoints, and a text-in/text-out turn.

---

### 2026-09-17 — Phase 1: Data layer ✅

**Built:** the full PostgreSQL schema, a migration runner, and seed data.

**Migration runner** — [scripts/migrate.py](scripts/migrate.py), ~140 lines, no framework.
Uses `uv`'s inline script dependencies (PEP 723), so there is no venv to manage: `uv run`
resolves `psycopg` on demand. It tracks applied migrations in `schema_migrations`, runs
each file in its own transaction, and **checksums every migration** — editing one that is
already applied is refused outright, since that is the usual way two databases silently
diverge. Verified by tampering with an applied file and watching it refuse.

**Schema** — 5 migrations, 11 tables, 12 enums:

| Migration | Tables |
|---|---|
| `0001_init` | enums + the shared `set_updated_at()` trigger function |
| `0002_customers_flights_bookings` | `customers`, `flights`, `bookings`, `refunds` |
| `0003_calls_conversations` | `calls`, `conversations` |
| `0004_jobs_failures` | `jobs`, `job_attempts`, `failures` |
| `0005_users_tickets` | `users`, `support_tickets` |

**Seed data** — [scripts/seed.py](scripts/seed.py), deterministic (`random.seed(42)`), covering
every table: 12 customers, 40 flights, 60 bookings, 80 calls with 415 conversation turns,
50 jobs with 90 attempts and 71 failures. The dashboard has realistic data to render
before the voice pipeline produces any real traffic.

**Decisions made:**

- **Native Postgres enums over `text` + `CHECK`.** Self-documenting in pgweb, rejected at
  the database boundary, one byte smaller. Cost: `ALTER TYPE ... ADD VALUE` to extend.
  Accepted — these state machines are the stable part of the design.
- **`calls.duration_ms` is a `GENERATED ALWAYS ... STORED` column.** Derived by Postgres
  from `started_at`/`ended_at`, so it cannot drift from the timestamps it comes from.
- **Per-stage latency (`stt_ms`, `llm_ms`, `tts_ms`) recorded on every turn from day one.**
  Phase 9 metrics and every "where is the time going" question need this, and
  backfilling it later would be impossible.
- **`conversations.providers` records which provider served each turn.** With swappable
  local/hosted providers, a latency regression must be attributable, not guessable.
- **Partial indexes on the hot polling paths** (`jobs_due_idx`, `jobs_retry_idx`,
  `calls_needs_attention_idx`). The scheduler and retry sweeper poll constantly but only
  ever want a thin slice of the table.
- **`idempotency_key UNIQUE` on `jobs`.** Enqueue twice with the same key and you get the
  original job back, not a second cancellation of the same booking (overview §1).
- **`job_attempts` as a separate table** (not in the overview). `jobs` holds current state;
  this holds the story — which attempt, after how much backoff, failing how. The
  dashboard's retry-history view depends on it.

**Bug found and fixed during verification:** the first seed run produced a dead-lettered
job whose attempts included `BookingNotFound` — a PERMANENT error — retried five times.
That directly contradicts the rule this entire system exists to enforce: permanent errors
must never be retried. Seed data is read while building the dashboard, so wrong data
teaches the wrong model. Fixed by splitting `RETRYABLE_MODES` from `PERMANENT_MODES`,
giving permanent failures exactly one attempt, and keeping one failing dependency per job
rather than a new random one per attempt. Now enforced by two invariant queries that both
return zero violations.

**Verified:**
- `make migrate` applies 5 migrations; re-running is a clean no-op
- Tampering with an applied migration is refused
- `make db-reset` rebuilds from zero to identical row counts (deterministic)
- Call analytics, queue-depth-by-priority, failures-by-service and retry-history queries
  all return sensible results — the four views the dashboard needs
- Backoff curve reads 0 / 2s / 4s / 8s / 16s, matching overview §9

**Next:** Phase 2 — the mock flight service with an injectable chaos engine.

---

### 2026-09-17 — Phase 0: Foundations ✅

**Built:** the empty-repo-to-running-stack baseline.

**Toolchain installed** (via Homebrew — you already had brew, git 2.54, make 3.81, Docker 29.7.2):

| Tool | Version | Purpose |
|---|---|---|
| `fnm` + Node | 22.23.2 | Node platform API (Phase 7), dashboard (Phase 8) |
| `uv` + Python | 3.12.14 | AI service + workers (system Python 3.9.6 was too old) |
| `ollama` | 0.34.0 | Local LLM, running as a brew service |
| `whisper-cpp` | 1.9.4 | Local Metal-accelerated STT |
| `piper-tts` | 1.8.0 | Local TTS |

**Models downloaded** into `models/` (gitignored):
`ggml-base.en.bin` (141M, fast), `ggml-small.en.bin` (465M, more accurate —
matters for PNRs and flight numbers), `en_US-lessac-medium.onnx` (60M Piper voice).
`qwen2.5:7b-instruct` pulled separately into Ollama's own store.

**Files created:**

| Path | Purpose |
|---|---|
| `Makefile` | Single entrypoint — `make help` lists everything |
| `docker-compose.yml` | Postgres 16, Redis 7 (AOF on), pgweb, RedisInsight |
| `.env.example` | Every knob, grouped by phase, with comments |
| `scripts/doctor.sh` | Health check across toolchain, models, services, config |
| `.gitignore` | Excludes `models/` (multi-GB) and `.env` |
| `docs/`, `db/migrations/`, `services/*`, `infra/*`, `tests/*` | Empty skeleton per the plan |

**Decisions made:**

- **Redis runs with `--appendonly yes`.** Queue state survives a restart. Without this,
  `docker compose restart` would silently swallow jobs and mask real bugs in Phase 6.
- **Both Whisper model sizes downloaded.** `base.en` for fast iteration, `small.en` for
  when transcription accuracy on alphanumerics starts to matter.
- **Admin UIs (pgweb, RedisInsight) included from day one.** Being able to *see* queue
  keys and table rows is most of the debugging experience in later phases.
- **`.env.example` documents settings for phases not built yet** (chaos, queue, auth).
  Cheaper than rediscovering the knobs later, and it doubles as a roadmap.

**Verified:**
- `make up` → all four containers healthy
- Postgres answers `select version()` → PostgreSQL 16.15
- Redis round-trips a SET/GET
- Ollama serving on :11434

**Next:** Phase 1 — SQL migrations for customers, flights, bookings, calls,
conversations, jobs, job_attempts, failures, users; plus a migration runner and seed data.
