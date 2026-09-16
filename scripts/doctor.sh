#!/usr/bin/env bash
# Verify every dependency VoiceOps needs is installed and reachable.
# Safe to run any time: it only reads state, never changes it.

set -uo pipefail
cd "$(dirname "$0")/.."

PASS=0; FAIL=0
ok()   { printf "  \033[32m✓\033[0m %-22s %s\n" "$1" "${2:-}"; PASS=$((PASS+1)); }
bad()  { printf "  \033[31m✗\033[0m %-22s %s\n" "$1" "${2:-}"; FAIL=$((FAIL+1)); }
warn() { printf "  \033[33m!\033[0m %-22s %s\n" "$1" "${2:-}"; }

have() { command -v "$1" >/dev/null 2>&1; }

echo ""
echo "  Toolchain"
have docker  && ok docker  "$(docker --version | cut -d, -f1)"            || bad docker "not installed"
have git     && ok git     "$(git --version | awk '{print $3}')"          || bad git "not installed"
have uv      && ok uv      "$(uv --version | awk '{print $2}')"           || bad uv "brew install uv"
have fnm     && ok fnm     "$(fnm --version | awk '{print $2}')"          || bad fnm "brew install fnm"
have ollama  && ok ollama  "$(ollama --version 2>/dev/null | awk '{print $NF}')" || bad ollama "brew install ollama"
have piper   && ok piper   "installed"                                    || bad piper "uv tool install piper-tts"
have whisper-cli && ok whisper-cli "installed"                            || bad whisper-cli "brew install whisper-cpp"
# whisper.cpp only accepts 16kHz mono WAV; browsers record WebM/Opus.
have ffmpeg  && ok ffmpeg  "$(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')" || bad ffmpeg "brew install ffmpeg"

echo ""
echo "  Runtimes"
if have node; then ok node "$(node -v)"; else warn node "run: eval \"\$(fnm env)\" && fnm use 22"; fi
uv python list 2>/dev/null | grep -q '3\.12' && ok "python 3.12" "via uv" || bad "python 3.12" "uv python install 3.12"

echo ""
echo "  Models"
for m in models/whisper/ggml-base.en.bin models/whisper/ggml-small.en.bin models/piper/en_US-lessac-medium.onnx; do
  if [ -f "$m" ]; then ok "$(basename "$m")" "$(du -h "$m" | cut -f1)"; else bad "$(basename "$m")" "run: make models"; fi
done
if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  if curl -s http://localhost:11434/api/tags | grep -q 'qwen2.5'; then ok "qwen2.5 (ollama)" "pulled"
  else bad "qwen2.5 (ollama)" "run: ollama pull qwen2.5:7b-instruct"; fi
else
  bad "ollama server" "run: brew services start ollama"
fi

echo ""
echo "  Services"
if docker info >/dev/null 2>&1; then
  ok "docker daemon" "running"
  for svc in postgres redis flight-mock; do
    state=$(docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk -v s="$svc" '$1==s{print $2}')
    [ "$state" = "running" ] && ok "$svc" "running" || warn "$svc" "not running — run: make up"
  done
else
  bad "docker daemon" "start Docker Desktop"
fi

echo ""
echo "  AI service"
if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
  ok "ai service" "$(curl -s http://localhost:8000/health | tr -d ' \n' | sed -n 's/.*"llm":"\([^"]*\)".*/\1/p')"
else
  warn "ai service" "not running — run: make ai"
fi

echo ""
echo "  Config"
[ -f .env ] && ok ".env" "present" || bad ".env" "run: make setup"

echo ""
printf "  %d passed, %d failed\n\n" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
