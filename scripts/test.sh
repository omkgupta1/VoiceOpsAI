#!/usr/bin/env bash
# Run the test suites, in the order that fails fastest.
#
#   make test              everything that does not need a real model
#   make test-unit         the fast pass: no database, no Redis, no services
#   make test-integration  the rest: needs `make up`
#
# Exit code is the number of suites that failed, so CI and a human get the same
# answer from the same command.
set -uo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-all}"
NODE_BIN="$HOME/.local/share/fnm/aliases/default/bin"
[ -d "$NODE_BIN" ] && export PATH="$NODE_BIN:$PATH"

GREEN=$'\033[32m'; RED=$'\033[31m'; DIM=$'\033[2m'; RESET=$'\033[0m'
FAILED=0
declare -a SUMMARY=()

run() {
  local name=$1; shift
  printf '\n  %s%s%s\n' "$DIM" "$name" "$RESET"
  if "$@"; then
    SUMMARY+=("${GREEN}pass${RESET}  $name")
  else
    SUMMARY+=("${RED}FAIL${RESET}  $name")
    FAILED=$((FAILED + 1))
  fi
}

in_dir() { local d=$1; shift; (cd "$d" && "$@"); }

if [ "$MODE" = "unit" ] || [ "$MODE" = "all" ]; then
  run "queue — unit"      in_dir packages/queue-py uv run pytest -m "not integration"
  run "ai — unit"         in_dir services/ai       uv run pytest -m "not integration and not model"
  run "api — RBAC matrix" in_dir services/api      npx vitest run src/auth
fi

if [ "$MODE" = "integration" ] || [ "$MODE" = "all" ]; then
  if ! docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null | grep -q 'running'; then
    printf '\n  %sIntegration tests need the stack — run `make up` first.%s\n\n' "$RED" "$RESET"
    exit 1
  fi
  run "queue — against Redis"    in_dir packages/queue-py uv run pytest -m integration
  run "ai — against the stack"   in_dir services/ai       uv run pytest -m integration
  run "api — against Postgres"   in_dir services/api      npx vitest run
fi

printf '\n'
for line in "${SUMMARY[@]}"; do printf '  %s\n' "$line"; done
printf '\n  %d/%d suites passed\n\n' "$((${#SUMMARY[@]} - FAILED))" "${#SUMMARY[@]}"
exit "$FAILED"
