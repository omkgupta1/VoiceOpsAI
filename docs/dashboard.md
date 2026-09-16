# Servicing dashboard

Next.js + Tailwind, `services/web/`. `make web`, then http://localhost:3001.

Sign in with any seeded account (password `voiceops123`):
`admin@` · `supervisor@` · `agent1@` · `agent2@voiceops.ai`.

Sign in as an agent and then as a supervisor to see RBAC working: the agent has
no Queue or Failures tab, because nav entries a role cannot use are hidden rather
than shown and then refused.

## Screens

| Screen | Shows | Needs |
|---|---|---|
| **Calls** | Totals, latency by stage, intent distribution, recent calls | `calls:read` (stats need `analytics:read`) |
| **Call detail** | Transcript with tool calls, per-turn latency, linked jobs, escalate | `calls:read` (+ `conversations:read` for the transcript) |
| **Queue** | Live depth by priority, in-flight, dead letters, attempt history | `jobs:read` (requeue needs `jobs:retry`) |
| **Failures** | Failures by service and class, retry rate, backoff | `failures:read` |
| **Console** | Push-to-talk or type, with tools and timings shown per turn | `voice:use` |

## Notes

- **The queue page polls every 3 seconds.** Jobs promote, retry and dead-letter
  with nobody touching the page; a static snapshot would mislead here in a way it
  does not on the calls list.
- **The latency panel exists to keep one fact visible**: the LLM dominates by an
  order of magnitude. Every optimisation instinct should go there first.
- **The attempt-history table's backoff column** shows the curve actually growing
  — 1.1s, 3.5s, 4.1s, 12.1s — rather than asking you to trust that it did.
- **Status colours are defined once** in `lib/ui.tsx`. `DEAD_LETTER` reading red
  on one screen and grey on another is the sort of thing that makes a dashboard
  untrustworthy.
- **The console replaced the AI service's public page.** That page was
  unauthenticated, and the AI service has no notion of who is calling — anything
  that could reach it could cancel bookings. It now binds loopback only and the
  platform API is the only way in.

## Token storage

The JWT is kept in `localStorage`. That is the pragmatic choice for a dashboard
behind a login, and the cost is worth stating plainly: any script running on this
origin can read it, so an XSS becomes a stolen session.

A production deployment should move to an `httpOnly` cookie set by a server
route, which JavaScript cannot read at all. Left as-is here because the change
touches auth on both sides and belongs with the hardening work in Phase 12.
