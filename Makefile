SHELL := /bin/bash
.DEFAULT_GOAL := help

# Isolated toolchain (uv-managed Python, pnpm, cargo). Override with TOOLCHAIN_DIR=/path.
# Default: nearest .toolchain in this directory or a parent (linked worktrees reuse the main checkout's).
TOOLCHAIN_DIR ?= $(shell d="$(CURDIR)"; while [ "$$d" != / ]; do [ -d "$$d/.toolchain" ] && echo "$$d/.toolchain" && break; d=$$(dirname "$$d"); done)
ifeq ($(TOOLCHAIN_DIR),)
TOOLCHAIN_DIR := $(CURDIR)/.toolchain
endif
export TOOLCHAIN_DIR
RUN := scripts/with-env.sh
DEVRUN := scripts/with-dev-env.sh

COMPOSE_PROJECT := rushbase-dev
SERVER_PORT := 18080
WEB_PORT := 15173
DB_PORT := 15432
COMPOSE_FILE := dev/docker-compose.dev.yml
DEV_ENV_FILE := dev/data/dev.env
PIDS := dev/data/pids

# `docker compose` plugin if present, else the pinned standalone binary from scripts/fetch-compose.sh.
COMPOSE = $(RUN) bash -c 'if docker compose version >/dev/null 2>&1; then exec docker compose "$$@"; else exec docker-compose "$$@"; fi' _ -p $(COMPOSE_PROJECT) -f $(COMPOSE_FILE) --env-file $(DEV_ENV_FILE)

.PHONY: help setup compose-bin dev dev-up dev-down lint typecheck test testmedia fmt gen-api build-web e2e-real

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

setup: ## Install Python (uv) and web (pnpm) dependencies
	$(RUN) uv sync
	$(RUN) pnpm install --frozen-lockfile

compose-bin: ## Fetch a standalone docker-compose into the toolchain if the host has none
	@$(RUN) bash -c 'docker compose version >/dev/null 2>&1 || bash scripts/fetch-compose.sh'

$(DEV_ENV_FILE):
	@mkdir -p $(dir $@)
	$(RUN) python3 scripts/write-dev-env.py $@ $(DB_PORT)

dev-up: compose-bin $(DEV_ENV_FILE) ## Start postgres + samba and apply migrations
	@mkdir -p dev/library
	$(COMPOSE) up -d --wait postgres samba
	$(DEVRUN) uv run alembic -c server/alembic.ini upgrade head

dev: dev-up ## Start the full dev stack (db, samba, server :18080, web :15173)
	@mkdir -p $(PIDS)
	@$(DEVRUN) uv run rushbase-server & echo $$! > $(PIDS)/server; \
	  trap 'kill $$(cat $(PIDS)/server) 2>/dev/null; rm -rf $(PIDS)' EXIT INT TERM; \
	  for i in $$(seq 1 60); do curl -fsS http://127.0.0.1:$(SERVER_PORT)/health >/dev/null 2>&1 && break; sleep 1; done; \
	  curl -fsS http://127.0.0.1:$(SERVER_PORT)/health >/dev/null && echo "[OK] server healthy on :$(SERVER_PORT)" && \
	  $(RUN) pnpm --filter web dev

dev-down: compose-bin $(DEV_ENV_FILE) ## Stop the dev stack (only project rushbase-dev)
	@if [ -d $(PIDS) ]; then kill $$(cat $(PIDS)/server) 2>/dev/null || true; rm -rf $(PIDS); fi
	-$(COMPOSE) down

lint: ## ruff + eslint + prettier check
	$(RUN) uv run ruff check .
	$(RUN) uv run ruff format --check .
	$(RUN) pnpm -r lint
	$(RUN) pnpm format:check

typecheck: ## mypy (strict) + tsc
	$(RUN) uv run mypy
	$(RUN) pnpm -r typecheck

test: ## pytest + vitest (set RUSHBASE_TEST_DATABASE_URL to include postgres tests)
	$(RUN) uv run pytest
	$(RUN) pnpm -r test

testmedia: ## Generate synthetic test media into dev/library/Input
	$(RUN) uv run python dev/testmedia/generate.py

fmt: ## Auto-format Python and web sources
	$(RUN) uv run ruff check --fix .
	$(RUN) uv run ruff format .
	$(RUN) pnpm format

gen-api: ## Regenerate packages/api-client from the server OpenAPI schema
	$(RUN) pnpm gen:api

build-web: ## Production build of the web UI
	$(RUN) pnpm --filter web build

e2e-real: dev-up ## Browser test of the web UI against a real server, database and worker (:18083)
	$(RUN) scripts/e2e-real.sh
