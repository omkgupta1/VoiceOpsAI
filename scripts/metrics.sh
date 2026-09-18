#!/usr/bin/env bash
# What each service is currently exposing to Prometheus.
set -uo pipefail

show() {
  local name=$1 url=$2
  local body
  if ! body=$(curl -sf --max-time 3 "$url" 2>/dev/null); then
    printf '  \033[31m%-12s\033[0m unreachable at %s\n' "$name" "$url"
    return
  fi
  local count
  count=$(printf '%s' "$body" | grep -c '^voiceops' || true)
  printf '  \033[32m%-12s\033[0m %s series\n' "$name" "$count"
  printf '%s' "$body" | grep '^voiceops' | grep -v ' 0$' | head -6 | sed 's/^/      /'
}

echo ""
show ai          "http://127.0.0.1:8000/metrics"
show worker      "http://127.0.0.1:9101/metrics"
show api         "http://127.0.0.1:3000/metrics"
show flight-mock "http://localhost:8002/metrics"
echo ""
echo "  Prometheus targets: http://localhost:9090/targets"
echo ""
