.PHONY: help install lock upgrade sync format format-check lint lint-ci lint-fix lint-loc typecheck typecheck-fresh test test-fast test-unit test-integration test-cov test-all check ci-local precommit clean dev mcp-serve mcp-serve-http run-prod docker-build docker-up docker-down docker-logs

.DEFAULT_GOAL := help

DOCKER_COMPOSE := $(shell if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then echo "docker compose"; elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose"; else echo "docker compose"; fi)

PKG := spliceailookup_link

help: ## Display this help message
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install: ## Install project and development dependencies with uv
	uv sync --group dev

sync: install ## Alias for install

lock: ## Resolve and update uv.lock
	uv lock

upgrade: ## Upgrade locked dependencies
	uv lock --upgrade

format: ## Format Python code
	uv run ruff format $(PKG) tests server.py mcp_server.py

format-check: ## Check formatting without writing
	uv run ruff format --check $(PKG) tests server.py mcp_server.py

lint: ## Lint Python code
	uv run ruff check $(PKG) tests server.py mcp_server.py

lint-ci: ## Lint Python code without modifying files
	uv run ruff check $(PKG) tests server.py mcp_server.py --output-format=github

lint-fix: ## Lint and apply safe fixes
	uv run ruff check $(PKG) tests server.py mcp_server.py --fix

lint-loc: ## Enforce per-file line budget (see AGENTS.md "File Size Discipline")
	uv run python scripts/check_file_size.py

typecheck: ## Type check package
	uv run mypy $(PKG) server.py mcp_server.py

typecheck-fresh: ## Clear mypy cache and run typecheck
	rm -rf .mypy_cache
	uv run mypy $(PKG) server.py mcp_server.py

test: ## Run deterministic unit tests quickly
	uv run pytest tests/unit -q

test-fast: ## Run deterministic unit tests in parallel with pytest-xdist
	uv run pytest tests/unit -q -n auto

test-unit: ## Run unit tests in parallel
	uv run pytest tests/unit -q -n auto

test-integration: ## Run live integration tests against the SpliceAI/Pangolin APIs
	uv run pytest tests/integration -q -m integration

test-cov: ## Run unit tests with coverage
	uv run pytest tests/unit --cov=$(PKG) --cov-report=term-missing --cov-report=html --cov-report=xml

test-all: test-cov ## Alias for full test run with coverage

check: format lint ## Format and lint

ci-local: format-check lint-ci lint-loc typecheck test-fast ## Run fast local CI-equivalent checks

precommit: ci-local ## Run checks expected before commit

clean: ## Remove local caches and generated reports
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml

dev: ## Run FastAPI host (/health) + mounted MCP HTTP locally
	uv run python server.py --transport unified --host 127.0.0.1 --port 8603

mcp-serve: ## Start local stdio MCP server
	uv run python mcp_server.py

mcp-serve-http: dev ## Alias: FastAPI host (/health) + mounted MCP HTTP locally

run-prod: ## Run production server with uvicorn
	uv run python server.py --transport unified --host 0.0.0.0 --port 8603

docker-build: ## Build Docker image
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml build

docker-up: ## Start Docker stack
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml up -d

docker-down: ## Stop Docker stack
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml down

docker-logs: ## Follow Docker logs
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml logs -f
