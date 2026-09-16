# VoiceOps AI — Progress Log

> Running context for this project. The header below is **current state**; the log
> further down is append-only history, newest first. Updated after every change.

---

## Current state

| | |
|---|---|
| **Phase** | 2 — Mock flight service + chaos engine ✅ complete |
| **Next** | Phase 3 — AI service: provider interfaces + text pipeline |
| **What runs today** | Postgres + Redis + the flight service, all in Docker Compose |

### How to start everything

```bash
make setup     # once: creates .env from .env.example
make models    # once: downloads Whisper + Piper models (~670MB, skips existing)
make up        # start Postgres, Redis, pgweb, RedisInsight
make migrate   # apply the SQL schema
make seed      # load sample data
make doctor    # verify the whole toolchain is healthy
```

`make db-reset` rebuilds the database from zero (drop → migrate → seed) when you
want a clean slate. Seeded dashboard logins are `admin@voiceops.ai`,
`supervisor@voiceops.ai`, `agent1@voiceops.ai`, `agent2@voiceops.ai`, all with
password `voiceops123`.

| Service | URL |
|---|---|
| pgweb (Postgres UI) | http://localhost:8081 |
| RedisInsight | http://localhost:5540 |
| Postgres | `localhost:5432` — `voiceops` / `voiceops` / db `voiceops` |
| Redis | `localhost:6379` |
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
- **Chaos persists until you turn it off.** If the flight service starts returning 503s
  unexpectedly, run `make chaos-status` — you probably left a scenario applied.

---

## Log

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
