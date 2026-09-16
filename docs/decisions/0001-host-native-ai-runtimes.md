# 0001 — Run Ollama and whisper.cpp on the host, not in Docker

**Status:** Accepted (Phase 0)

## Context

Everything else in this project is containerised, so the obvious move is to containerise
the model runtimes too. But Docker Desktop on macOS runs containers inside a Linux VM
with **no Metal/GPU passthrough**. A containerised Ollama or whisper.cpp would fall back
to CPU-only inference inside a VM — several times slower, which is fatal for a system
whose whole point is conversational latency.

## Decision

Ollama and whisper.cpp run natively on the macOS host. Containers reach them via
`host.docker.internal`, configured through `OLLAMA_BASE_URL`.

## Consequences

- Local inference gets full Metal acceleration on the M5.
- `docker compose up` alone is **not** sufficient — Ollama must be running too. This is
  why `make doctor` checks it explicitly.
- Provider config must never hardcode `localhost`; it is host-relative and differs
  between "running on the host" and "running in a container".
- On Linux or in AWS this reverses cleanly: the hosted providers (Groq/Gemini) or a
  GPU node take over, which is exactly why the provider interface exists.
