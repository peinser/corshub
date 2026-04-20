# ===============================================
# Makefile for the project
# ===============================================

# ───────────────────────────────────────────────
# Configuration & Variables
# ───────────────────────────────────────────────

# Default Python interpreter (uv-managed)
PYTHON          := python
UV              := uv
PROJECT_ENV     := /usr/local/

# Common flags
.DEFAULT_GOAL   := help
MAKEFLAGS       += --no-print-directory
SHELL           := bash
.ONESHELL:      # all commands in a target run in a single shell

# Colors for output
NO_COLOR        := \033[0m
OK_COLOR        := \033[32;01m
ERROR_COLOR     := \033[31;01m
WARN_COLOR      := \033[33;01m
INFO_COLOR      := \033[36;01m

# ───────────────────────────────────────────────
# Main targets
# -----------------------------------------------

.PHONY: help
help: ## Show this help message
	@echo -e "$(INFO_COLOR)Makefile help:$(NO_COLOR)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(OK_COLOR)%-20s$(NO_COLOR) %s\n", $$1, $$2}'

.PHONY: setup
setup: install ## Setup the project (install dependencies)
	@echo -e "$(OK_COLOR)Project setup complete$(NO_COLOR)"

.PHONY: install
install: ## Install Python dependencies using uv (locked)
	@echo -e "$(INFO_COLOR)Installing Python dependencies...$(NO_COLOR)"
	$(UV) sync --locked
	@echo -e "$(OK_COLOR)Dependencies installed successfully$(NO_COLOR)"

.PHONY: sync
sync: ## Re-sync dependencies (after changing pyproject.toml / uv.lock)
	@echo -e "$(INFO_COLOR)Syncing dependencies...$(NO_COLOR)"
	$(UV) sync --locked
	@echo -e "$(OK_COLOR)Dependencies synced$(NO_COLOR)"

.PHONY: lock
lock: ## Update uv.lock file (after adding/removing dependencies)
	@echo -e "$(INFO_COLOR)Updating uv.lock...$(NO_COLOR)"
	$(UV) lock
	@echo -e "$(OK_COLOR)Lock file updated$(NO_COLOR)"

.PHONY: clean
clean: ## Remove Python cache files, __pycache__, etc.
	@echo -e "$(INFO_COLOR)Cleaning Python caches...$(NO_COLOR)"
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.py[cod]" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .coverage htmlcov .ruff_cache .mypy_cache
	@echo -e "$(OK_COLOR)Clean complete$(NO_COLOR)"

.PHONY: lint
lint: ## Run linters (ruff + mypy)
	@echo -e "$(INFO_COLOR)Running linters...$(NO_COLOR)"
	$(UV) run ruff check src/corshub
	@echo -e "$(OK_COLOR)Linting passed$(NO_COLOR)"

.PHONY: format
format: ## Auto-format code with ruff
	@echo -e "$(INFO_COLOR)Formatting code...$(NO_COLOR)"
	$(UV) run ruff format .
	@echo -e "$(OK_COLOR)Formatting complete$(NO_COLOR)"

.PHONY: test
test: ## Run tests with coverage
	@echo -e "$(INFO_COLOR)Running tests...$(NO_COLOR)"
	$(UV) run pytest --cov=src/corshub --cov-report=term-missing
	@echo -e "$(OK_COLOR)Tests complete$(NO_COLOR)"

.PHONY: dev
dev: ## Run the application in development mode (if applicable)
	uv run python -m corshub.bin.standalone --host=0.0.0.0 --access-log --debug --reload --workers=1

.PHONY: helm-sync-policies
helm-sync-policies: ## Replace dev-time symlinks in helm/files/opa/policies/ with real copies for self-contained chart packaging (run automatically by CI before helm package)
	@echo -e "$(INFO_COLOR)Syncing OPA policies into Helm chart files...$(NO_COLOR)"
	find helm/files/opa/policies -maxdepth 1 -name '*.rego' -delete
	cp src/opa/policies/corshub/*.rego helm/files/opa/policies/
	@echo -e "$(OK_COLOR)OPA policies synced$(NO_COLOR)"

.PHONY: all
all: clean install lint test ## Run the full CI-like pipeline locally

# ───────────────────────────────────────────────
# Local GitHub Actions with act
# ───────────────────────────────────────────────

.PHONY: act
act: ## Run GitHub Actions workflows locally using act (requires GITHUB_TOKEN). Example: make act
	@echo -e "$(INFO_COLOR)Running GitHub Actions locally with act...$(NO_COLOR)"
	@test -n "$$GITHUB_TOKEN" || (echo -e "$(ERROR_COLOR)Environment variable GITHUB_TOKEN is not set. Create a scoped PAT and export GITHUB_TOKEN as described in CONTRIBUTING.md$(NO_COLOR)" && exit 1)
	@command -v act >/dev/null 2>&1 || (echo -e "$(ERROR_COLOR)act not found in PATH. Use the project's devcontainer or install act$(NO_COLOR)" && exit 1)
	act -P on-prem=harbor.peinser.com/library/github-actions-runner:latest -s GITHUB_TOKEN="$$GITHUB_TOKEN"

.PHONY: act-job
act-job: ## Run a specific job from the workflows via act. Usage: make act-job JOB=<job-name> [PLATFORM=<platform>]
	@echo -e "$(INFO_COLOR)Running job '$(JOB)' with act...$(NO_COLOR)"
	@test -n "$(JOB)" || (echo -e "$(ERROR_COLOR)Please set JOB variable (e.g., make act-job JOB=test)$(NO_COLOR)" && exit 1)
	@test -n "$$GITHUB_TOKEN" || (echo -e "$(ERROR_COLOR)GITHUB_TOKEN is not set. See CONTRIBUTING.md$(NO_COLOR)" && exit 1)
	@command -v act >/dev/null 2>&1 || (echo -e "$(ERROR_COLOR)act not found in PATH. Use the project's devcontainer or install act$(NO_COLOR)" && exit 1)
	: $${PLATFORM:="on-prem=harbor.peinser.com/library/github-actions-runner:latest"}; \
	act -P "$$PLATFORM" -j "$(JOB)" -s GITHUB_TOKEN="$$GITHUB_TOKEN"

# ───────────────────────────────────────────────
# Development tools
# ───────────────────────────────────────────────



# ───────────────────────────────────────────────
# 3th Party NTRIP Client
# ───────────────────────────────────────────────

.PHONY: gnssntripclient
NTRIP_SERVER     ?= corshub.peinser.com
NTRIP_PORT       ?= 443
NTRIP_HTTPS      ?= 1
NTRIP_MOUNTPOINT ?= MOBILE-PEINSER-0
NTRIP_VERSION    ?= 2.0
NTRIP_USER       ?= anonymous
NTRIP_PASSWORD   ?= anonymous
NTRIP_ARGS       ?=
gnssntripclient: ## Run the NTRIP client (SEMU Consulting). Override vars or pass NTRIP_ARGS. Example: make gnssntripclient NTRIP_SERVER=localhost NTRIP_PORT=8000 NTRIP_HTTPS=0
	@echo -e "$(INFO_COLOR)Running external NTRIP client...$(NO_COLOR)"
	gnssntripclient -S $(NTRIP_SERVER) -P $(NTRIP_PORT) --https $(NTRIP_HTTPS) -M $(NTRIP_MOUNTPOINT) --ntripversion $(NTRIP_VERSION) --ntripuser $(NTRIP_USER) --ntrippassword $(NTRIP_PASSWORD) $(NTRIP_ARGS) --verbosity 3