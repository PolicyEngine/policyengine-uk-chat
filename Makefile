.PHONY: up down build logs restart shell-backend shell-frontend

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
