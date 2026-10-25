SHELL := /bin/bash
.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help env up down ps logs check fmt test-integration spike-a spike-b spikes schema seed e2e mcp

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

env: ## Create .env (or add missing settings) with random secrets
	@uv run python scripts/make_env.py

up: env ## Start the platform and wait until healthy
	$(COMPOSE) up -d --build --wait

down: ## Stop the platform (keeps volumes)
	$(COMPOSE) down

ps: ## Show service status
	$(COMPOSE) ps

logs: ## Tail service logs
	$(COMPOSE) logs -f --tail=100

check: ## Lint, format check, type check, unit tests
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run pytest -q

fmt: ## Apply ruff fixes and formatting
	uv run ruff check --fix .
	uv run ruff format .

test-integration: ## Integration tests against the running stack
	uv run pytest -q -m integration

spike-a: ## Spike A: concurrent DuckLake writers in two processes
	$(COMPOSE) run --rm --no-deps -v ./spikes:/app/spikes:ro worker python /app/spikes/spike_a.py

spike-b: ## Spike B: dbt v2 builds a model into DuckLake
	$(COMPOSE) run --rm --no-deps -v ./spikes:/app/spikes:ro worker bash /app/spikes/spike_b/run.sh

spikes: spike-a spike-b ## Run both spikes

schema: ## Regenerate the bundle JSON schema (needs the Databricks CLI)
	databricks bundle schema > schemas/bundle_config_schema.json
	databricks --version

seed: ## Write the demo landing data to s3://landing/shop/
	uv run python scripts/seed_landing.py

e2e: up seed ## End-to-end acceptance test (SPEC section 12): seed, deploy, run, query, destroy
	uv run pytest -q -m e2e tests/e2e

mcp: ## Run the MCP server on stdio (Claude Code starts it from .mcp.json)
	uv run dbk-mcp
