.PHONY: up down build logs restart shell-backend shell-frontend test test-backend test-frontend sync-policyengine-uk-evals check-policyengine-uk-evals eval-ai-offline eval-ai-live eval-ai-live-uk-population

# Start all services in dev mode (live reload)
up:
	docker compose up

# Start detached
up-d:
	docker compose up -d

# Stop and remove containers
down:
	docker compose down

# Rebuild images (use after requirements/package.json changes)
build:
	docker compose build

# Rebuild and restart
rebuild:
	docker compose down && docker compose build && docker compose up

# Tail logs for all services
logs:
	docker compose logs -f

# Tail logs for a specific service: make logs-backend
logs-backend:
	docker compose logs -f backend

logs-frontend:
	docker compose logs -f frontend

# Open shell in backend container
shell-backend:
	docker compose exec backend bash

# Open shell in frontend container
shell-frontend:
	docker compose exec frontend sh

# One-time setup: copy .env.example to .env
init:
	@if [ ! -f .env ]; then cp .env.example .env && echo "Created .env — fill in your ANTHROPIC_API_KEY"; else echo ".env already exists"; fi

# Run the same checks used by PR CI, assuming dependencies are already installed.
test: test-backend test-frontend

test-backend:
	PYTHONPATH=backend python -m pytest backend/tests --cov --cov-config=.coveragerc --cov-report=term-missing --cov-report=xml --cov-fail-under=80

test-frontend:
	cd frontend && npm run test:coverage
	cd frontend && npm run build

sync-policyengine-uk-evals:
	PYTHONPATH=backend python -m eval.sync_policyengine_uk --sync

check-policyengine-uk-evals:
	PYTHONPATH=backend python -m eval.sync_policyengine_uk --check

eval-ai-offline: check-policyengine-uk-evals
	PYTHONPATH=backend python -m eval.run --mode offline

eval-ai-live: check-policyengine-uk-evals
	PYTHONPATH=backend python -m eval.run --mode live --provider anthropic

eval-ai-live-uk-population: check-policyengine-uk-evals
	RUN_DATA_EVALS=1 PYTHONPATH=backend python -m eval.run --suite tool_loop --mode live --provider anthropic
