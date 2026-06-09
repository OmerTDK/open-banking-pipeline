.DEFAULT_GOAL := help

.PHONY: help install lint test ci docker-build docker-test

help: ## List available targets
	@grep -E '^[a-zA-Z][a-zA-Z0-9_-]*:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "%-16s %s\n", $$1, $$2}'

install: ## Install dependencies into .venv
	uv sync

lint: ## Ruff lint and format check
	uv run ruff check .
	uv run ruff format --check .

test: ## Run the test suite
	uv run pytest -v

ci: lint test ## Run the full CI suite locally

docker-build: ## Build the project image
	docker build -t open-banking-pipeline .

docker-test: ## Run the test suite inside the image
	docker run --rm open-banking-pipeline
