# VoiceOps AI — Progress Log

> Running context for this project. The header below is **current state**; the log
> further down is append-only history, newest first. Updated after every change.

---

## Current state

| | |
|---|---|
| **Phase** | 0 — Foundations ✅ complete |
| **Next** | Phase 1 — Data layer (SQL migrations + seed data) |
| **What runs today** | Postgres 16 + Redis 7 + admin UIs, via Docker Compose |

### How to start everything

```bash
make setup     # once: creates .env from .env.example
make models    # once: downloads Whisper + Piper models (~670MB, skips existing)
make up        # start Postgres, Redis, pgweb, RedisInsight
make doctor    # verify the whole toolchain is healthy
```

| Service | URL |
|---|---|
| pgweb (Postgres UI) | http://localhost:8081 |
| RedisInsight | http://localhost:5540 |
| Postgres | `localhost:5432` — `voiceops` / `voiceops` / db `voiceops` |
| Redis | `localhost:6379` |
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

---

## Log

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
