# Testing and CI

```bash
make test-unit          # ~2s, needs nothing running
make test               # + integration, needs `make up`
make test-integration   # just the ones that need the stack
make lint               # ruff + tsc, exactly what CI runs
make test-load          # k6 against a running API
```

| Suite | Count | Needs | Where |
|---|---|---|---|
| Queue — unit | 27 | nothing | `packages/queue-py/tests/` |
| Queue — Redis | 10 | Redis | same, marked `integration` |
| AI service | 26 | nothing | `services/ai/tests/` |
| API — RBAC | 52 | nothing | `services/api/src/auth/rbac.test.ts` |
| API — HTTP | 15 | Postgres + Redis | `services/api/src/app.integration.test.ts` |
| Voice loop | — | a real model | `make test-voice`, local only |

## What is not tested here, and why

**The model.** Every test uses a scripted `LLMProvider`. That is not a shortcut:
the confirmation gate must hold for *any* model output, including a model that
has been talked into ignoring its instructions, so testing it against a
cooperative model would prove nothing. It also keeps the suite free of a 5GB
download and a GPU.

The thing a scripted provider cannot check is whether the model still picks the
*right* tool — which is exactly what degrades when a prompt is edited. That is
measured by `.github/workflows/nightly-eval.yml` against a hosted free tier,
nightly and informational: a model having an off night reports a number rather
than blocking a merge.

**Load, in CI.** The bottleneck here is one local 7B model serving roughly one
turn at a time. A GitHub runner has no model at all, so a number from CI would
measure the runner.

## Choices worth knowing

**Redis is real, never mocked.** The properties under test — atomic reservation,
lease expiry, priority ordering — *are* Redis semantics. A mock would assert only
that the mock behaves as I imagined Redis does.

**The API is driven through `app.inject()`.** It runs a request through auth,
RBAC, routing and the error handler in-process, with no port to race against.
Starting a server and hoping to connect in time is the usual source of flaky CI.
This is why `src/app.ts` exists as a factory separate from `src/index.ts`.

One limit worth stating: `inject` opens no socket, so the HTTP instrumentation
creates no server span and there is no `x-trace-id` on the response. That header
is asserted against a real socket by `make test-trace`.

**The RBAC matrix is written out by hand** in the test, rather than derived from
`ROLE_PERMISSIONS`. A test that builds its expectation from the code under test
asserts only that the code equals itself.

**Unit tests run with nothing else alive.** That is the point of the separate
job: if the unit pass starts needing a database, something has leaked out of the
integration suite.

## CI

`.github/workflows/ci.yml`, on every push and pull request. Four jobs:

| Job | Does | Needs |
|---|---|---|
| `lint` | ruff, tsc on both TypeScript packages | — |
| `unit` | the three no-service suites | — |
| `integration` | migrate, seed, then the suites that need data | postgres + redis services |
| `build` | flight-service image, dashboard build, compose validation | — |

Postgres and Redis come from GitHub's **service containers** rather than
testcontainers. The runner starts and health-checks them in about five seconds,
which keeps the loop short enough that people actually wait for it; testcontainers
would add 20-30s per run to buy portability off GitHub Actions that this repo
does not need yet.

Two things the CI catches that local runs cannot:

- **`make vendor` drift.** The flight service builds from a copied telemetry
  package. A stale copy is invisible locally because one is already present.
- **Migrations are idempotent.** They are run twice; the checksum guard refuses
  an already-applied migration that has since been edited.

### The nightly eval needs a secret

`GROQ_API_KEY`, under Settings → Secrets → Actions. Without it the workflow
**skips** rather than failing — a red X on a fork for a credential it was never
meant to have is noise.

## Formatting is not enforced yet

`ruff check` runs in CI; `ruff format --check` does not. Running the formatter
today would rewrite 38 files and 2,463 lines, burying real changes in review.
`E501` is ignored in `ruff.toml` for the same reason — line length belongs to the
formatter, not the linter.

To close it: run `uvx ruff format packages services scripts` as its own change,
then add the check to CI and drop the `E501` ignore.
