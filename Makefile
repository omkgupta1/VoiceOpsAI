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

.PHONY: help doctor setup models up down restart ps logs psql redis clean nuke

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

up: ## Start the local stack (Postgres, Redis, admin UIs)
	@$(COMPOSE) up -d --wait
	@echo ""
	@$(COMPOSE) ps
	@echo ""
	@echo "  pgweb        http://localhost:8081"
	@echo "  RedisInsight http://localhost:5540"

down: ## Stop the stack (data volumes are preserved)
	@$(COMPOSE) down

restart: ## Restart the stack
	@$(MAKE) down && $(MAKE) up

ps: ## Show container status
	@$(COMPOSE) ps

logs: ## Tail logs from all services
	@$(COMPOSE) logs -f --tail=100

psql: ## Open a psql shell against the local database
	@$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-voiceops} -d $${POSTGRES_DB:-voiceops}

redis: ## Open a redis-cli shell
	@$(COMPOSE) exec redis redis-cli

clean: ## Stop the stack and delete its data volumes (keeps models)
	@$(COMPOSE) down -v

nuke: ## clean + delete downloaded models
	@$(MAKE) clean
	@rm -rf $(MODELS_DIR)
