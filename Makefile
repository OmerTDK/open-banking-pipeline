.DEFAULT_GOAL := help

.PHONY: help install lint test ingest e2e mart contracts-generate contracts-check ci docker-build docker-test

E2E_DATABASE := /tmp/open-banking-e2e.duckdb
E2E_SECOND_RUN_LOG := /tmp/open-banking-e2e-second-run.log
E2E_BANK_COUNT := 3

help: ## List available targets
	@grep -E '^[a-zA-Z][a-zA-Z0-9_-]*:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "%-16s %s\n", $$1, $$2}'

install: ## Install dependencies into .venv
	uv sync

lint: ## Ruff lint and format check
	uv run ruff check .
	uv run ruff format --check .

test: ## Run the test suite
	uv run pytest -v

ingest: ## Ingest all mock banks into data/local/landing.duckdb (with fault injection)
	uv run open-banking-ingest --failure-seed 7

e2e: ## End-to-end ingestion into a fresh throwaway store; the second run must land zero new rows, mart prints spend summary
	rm -f $(E2E_DATABASE)
	uv run open-banking-ingest --database $(E2E_DATABASE) --failure-seed 7
	uv run open-banking-ingest --database $(E2E_DATABASE) > $(E2E_SECOND_RUN_LOG)
	cat $(E2E_SECOND_RUN_LOG)
	test "$$(grep -c 'accounts +0  transactions +0' $(E2E_SECOND_RUN_LOG))" -eq $(E2E_BANK_COUNT)
	uv run open-banking-mart --database $(E2E_DATABASE)

mart: ## Print spend-by-category-by-month from data/local/landing.duckdb
	uv run open-banking-mart

contracts-generate: ## Regenerate the committed contract artifacts from code
	uv run open-banking-contracts generate

contracts-check: ## Fail on breaking or unregenerated contract changes (CI gate)
	uv run open-banking-contracts check --require-fresh

ci: lint test contracts-check e2e ## Run the full CI suite locally

docker-build: ## Build the project image
	docker build -t open-banking-pipeline .

docker-test: ## Run the test suite inside the image
	docker run --rm open-banking-pipeline
