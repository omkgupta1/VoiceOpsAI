# VoiceOps AI — single entrypoint for every common task.
# Run `make` or `make help` to see what's available.

SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := docker compose
MODELS_DIR := models
WHISPER_BASE := $(MODELS_DIR)/whisper/ggml-base.en.bin
WHISPER_SMALL := $(MODELS_DIR)/whisper/ggml-small.en.bin
PIPER_VOICE := $(MODELS_DIR)/piper/en_US-lessac-medium.onnx
HF := https://huggingface.co
FLIGHT_MOCK_PORT ?= 8002
AI_PORT ?= 8000
S ?= flaky
RUNS ?= 1
MODE ?= scoped
AI_DIR := services/ai
WORKER_DIR := services/worker
QUEUE_DIR := packages/queue-py
API_DIR := services/api
WEB_DIR := services/web
# node is installed via fnm, which is a shell function; call its binary directly.
NODE_ENV_SETUP := export PATH="$$HOME/.local/share/fnm/aliases/default/bin:$$PATH"

.PHONY: help doctor setup models up down restart ps logs psql redis clean nuke \
        vendor traces grafana metrics test-trace \
        migrate migrate-status seed reference db-reset chaos chaos-status chaos-off \
        ai eval test-gate test-voice worker queue test-queue api test-rbac web

help: ## Show this help
	@echo ""
	@echo "  VoiceOps AI — available commands"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
	@echo ""

doctor: ## Check that every required tool is installed and running
	@bash scripts/doctor.sh

setup: ## First-time setup: create .env from the template
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env from .env.example"; \
	  else echo ".env already exists — leaving it alone"; fi

models: ## Download Whisper + Piper speech models (~1.1GB, skips existing)
	@mkdir -p $(MODELS_DIR)/whisper $(MODELS_DIR)/piper
	@test -f $(WHISPER_BASE)  || curl -#L -o $(WHISPER_BASE)  $(HF)/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
	@test -f $(WHISPER_SMALL) || curl -#L -o $(WHISPER_SMALL) $(HF)/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin
	@test -f $(PIPER_VOICE)   || curl -#L -o $(PIPER_VOICE)   $(HF)/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
	@test -f $(PIPER_VOICE).json || curl -#L -o $(PIPER_VOICE).json $(HF)/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
	@echo "Models ready:"; du -sh $(MODELS_DIR)/*

up: vendor ## Start the local stack (datastores, admin UIs, observability)
	@$(COMPOSE) up -d --wait
	@echo ""
	@$(COMPOSE) ps
	@echo ""
	@echo "  pgweb        http://localhost:8081"
	@echo "  RedisInsight http://localhost:5540"
	@echo "  Jaeger       http://localhost:16686"
	@echo "  Prometheus   http://localhost:9090"
	@echo "  Grafana      http://localhost:3002"

down: ## Stop the stack (data volumes are preserved)
	@$(COMPOSE) down

restart: ## Restart the stack
	@$(MAKE) down && $(MAKE) up

ps: ## Show container status
	@$(COMPOSE) ps

logs: ## Tail logs from all services
	@$(COMPOSE) logs -f --tail=100

migrate: ## Apply pending database migrations
	@uv run scripts/migrate.py

migrate-status: ## Show which migrations are applied vs pending
	@uv run scripts/migrate.py status

reference: ## Load real airports, airlines and routes from OpenFlights (cached)
	@uv run scripts/load_reference.py $(if $(REFRESH),--refresh,)

seed: ## Load sample calls, bookings and jobs (needs `make reference` first)
	@uv run scripts/seed.py

db-reset: ## Drop everything and rebuild: migrate + seed from scratch
	@echo "  Dropping schema public..."
	@$(COMPOSE) exec -T postgres psql -q -U $${POSTGRES_USER:-voiceops} -d $${POSTGRES_DB:-voiceops} \
	  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
	@$(MAKE) migrate
	@$(MAKE) reference
	@$(MAKE) seed


chaos: ## Break the flight service: make chaos S=flaky|hard_down|slow|rate_limited|timeouts|corrupt
	@curl -sf -X POST http://localhost:$(FLIGHT_MOCK_PORT)/admin/chaos/scenario/$(S) > /dev/null \
	  && echo "  chaos scenario applied: $(S)" \
	  || echo "  failed — is the flight service up? (make up)"

chaos-status: ## Show the current chaos configuration and hit counters
	@curl -sf http://localhost:$(FLIGHT_MOCK_PORT)/admin/chaos | python3 -m json.tool \
	  || echo "  flight service is not reachable on :$(FLIGHT_MOCK_PORT)"

chaos-off: ## Turn chaos off and restore healthy behaviour
	@curl -sf -X POST http://localhost:$(FLIGHT_MOCK_PORT)/admin/chaos/reset > /dev/null \
	  && echo "  chaos disabled" \
	  || echo "  failed — is the flight service up? (make up)"


ai: ## Run the AI service (internal, loopback only — the API is the way in)
	@cd $(AI_DIR) && uv run uvicorn app.main:app --host 127.0.0.1 --port $(AI_PORT) --reload

eval: ## Measure tool selection (RUNS=3 to stabilise, MODE=compare vs unscoped)
	@cd $(AI_DIR) && uv run python eval_tools.py --runs $(RUNS) --mode $(MODE)

test-gate: ## Verify the confirmation gate still blocks unconfirmed booking changes
	@cd $(AI_DIR) && uv run python test_confirmation_gate.py

test-voice: ## Speak questions at the agent and check it hears and acts on them
	@cd $(AI_DIR) && uv run python test_voice_loop.py

web: ## Run the servicing dashboard
	@cd $(WEB_DIR) && $(NODE_ENV_SETUP) && npm run dev

api: ## Run the Node platform API (auth, calls, jobs, analytics)
	@cd $(API_DIR) && $(NODE_ENV_SETUP) && npm run dev

test-rbac: ## Verify the role/permission matrix exhaustively
	@cd $(API_DIR) && $(NODE_ENV_SETUP) && npm test

worker: ## Run the queue workers and scheduler
	@cd $(WORKER_DIR) && uv run python -m app.main

queue: ## Show queue depth, counters and dead letters (make queue W=1 to watch)
	@cd $(QUEUE_DIR) && uv run --quiet python ../../scripts/queue_status.py $(if $(W),--watch,)

test-queue: ## Verify priority, crash recovery, backoff, DLQ and idempotency
	@cd $(QUEUE_DIR) && uv run python test_queue.py


vendor: ## Copy the shared telemetry package into the flight service build context
	@rsync -a --delete --exclude '__pycache__' --exclude '*.egg-info' \
	  packages/telemetry-py/ services/flight-mock/vendor/telemetry-py/

traces: ## Open Jaeger
	@open http://localhost:16686

grafana: ## Open the Grafana dashboard
	@open http://localhost:3002/d/voiceops-overview

metrics: ## Show what each service is currently exposing to Prometheus
	@bash scripts/metrics.sh

test-trace: ## Prove one voice call spans every service in a single trace
	@uv run scripts/verify_trace.py

psql: ## Open a psql shell against the local database
	@$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-voiceops} -d $${POSTGRES_DB:-voiceops}

redis: ## Open a redis-cli shell
	@$(COMPOSE) exec redis redis-cli

clean: ## Stop the stack and delete its data volumes (keeps models)
	@$(COMPOSE) down -v

nuke: ## clean + delete downloaded models
	@$(MAKE) clean
	@rm -rf $(MODELS_DIR)
