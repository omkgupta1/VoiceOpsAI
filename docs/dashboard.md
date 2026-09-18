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

## Design system

`app/globals.css` holds every colour, animation and surface style. Components
never name a literal colour — they read a token — which is why the light theme
is one media query of overrides rather than a second stylesheet.

| Token | Used for |
|---|---|
| `--color-accent` (violet) | the product, primary actions, the LLM stage |
| `--color-accent2` (cyan) | identifiers, links, in-flight work, STT |
| `--color-ok` (emerald) | success, healthy, TTS |
| `--color-warn` (amber) | escalation, retry, retryable failures |
| `--color-bad` (rose) | failure, dead letter, permanent failures, HIGH priority |

Status colours come from `lib/ui.tsx` and resolve to **inline styles**, not
Tailwind classes. Tailwind cannot see a class name assembled at runtime, so
`text-[var(--color-${tone})]` compiles to nothing at all — and only for whichever
status happened not to be on screen while developing. `toneStyle()` returns a
style object, which cannot be tree-shaken away.

For the same reason, arbitrary values must spell out `var()`. Tailwind v4 dropped
the v3 shorthand, so `text-[--color-muted]` emits `color: --color-muted` — invalid
CSS that the browser silently drops, with no warning and a passing build. Write
`text-[var(--color-muted)]`.

### Motion

Every animation is defined in `globals.css` and wrapped by a
`prefers-reduced-motion` guard.

| Class / hook | Where |
|---|---|
| `.rise` | staggered entry, delayed by a `--i` custom property |
| `.lift` | 2px hover lift on cards |
| `.halo` | pulsing ring on the recording button |
| `.breathe` | dot on a status that means "running right now" |
| `.shimmer` | loading skeletons — the layout is final before data lands |
| `.bar-fill` | bars growing from zero to their measured width |
| `useCountUp` | queue stats, animating between values rather than from zero |
| `useFlashOnChange` | a queue number that just moved on its own |

Count-up and flash are on the **queue page only**, because that is the one screen
whose numbers change while nobody is touching it. Animating a number that only
moves on reload would be decoration.

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
