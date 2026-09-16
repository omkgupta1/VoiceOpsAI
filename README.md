# VoiceOps AI

A self-healing voice support platform for flight servicing: speech in, speech out, with
an LLM choosing and calling real backend tools — backed by a Redis priority queue, an
exponential-backoff retry engine, PostgreSQL analytics, an operations dashboard, and
automated failure remediation.

Built from scratch as a learning project. See [overview.md](overview.md) for the full
vision and [PROGRESS.md](PROGRESS.md) for what actually works today.

## Quickstart

```bash
make setup     # create .env from the template
make models    # download Whisper + Piper models (~670MB)
make up        # start Postgres, Redis, the flight service and the admin UIs
make migrate   # apply the SQL schema
make seed      # load sample data
make doctor    # verify the whole toolchain
```

Then open [the flight API docs](http://localhost:8002/docs),
[pgweb](http://localhost:8081) and [RedisInsight](http://localhost:5540).
`make help` lists every command.

Break things on purpose with `make chaos S=flaky` — see [docs/chaos.md](docs/chaos.md).

## Prerequisites

Docker Desktop, plus `brew install fnm uv ollama whisper-cpp` and
`uv tool install piper-tts`. `make doctor` tells you what is missing.

## Design in one diagram

```
Browser mic ──► AI service (FastAPI) ──► STT ──► LLM (tool calling) ──► TTS ──► audio back
                      │                                  │
                      │                                  └──► Flight service (mock, chaos-injectable)
                      ▼
              Redis priority queue ──► Worker + Scheduler ──► retry / backoff / DLQ
                      │                        │
                      ▼                        ▼
              Node platform API ──────────► PostgreSQL ──────► Dashboard (Next.js)
```

Everything runs locally first, then on a local `kind` cluster, then on AWS.

## Repository layout

| Path | Contents |
|---|---|
| `services/ai/` | FastAPI — providers, tools, flows, turn orchestrator |
| `services/api/` | Fastify + TypeScript — REST, auth, RBAC |
| `services/worker/` | Python — queue consumer, scheduler, retry engine |
| `services/flight-mock/` | Fake airline backend with an injectable chaos engine |
| `services/web/` | Next.js servicing dashboard |
| `db/migrations/` | Raw SQL — the single source of truth for the schema |
| `docs/` | Architecture, queue protocol, runbook, decision records |
| `infra/` | Docker, Kubernetes, Terraform, Lambda |

## AI providers

Each layer is swappable via `.env`, with a free local option and a free hosted option:

| Layer | `local` | Hosted |
|---|---|---|
| STT | whisper.cpp (Metal) | Groq `whisper-large-v3` |
| LLM | Ollama `qwen2.5:7b-instruct` | Groq or Gemini Flash |
| TTS | Piper | Gemini TTS |

> Ollama and whisper.cpp run **natively on the host**, not in Docker — Docker Desktop on
> macOS has no Metal passthrough. Containers reach them via `host.docker.internal`.
