.PHONY: db-up db-down backend-install backend-test backend-lint backend-typecheck migrate api frontend-install frontend-check web

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

backend-install:
	cd backend && uv sync

backend-test:
	cd backend && uv run pytest

backend-lint:
	cd backend && uv run ruff check . && uv run ruff format --check .

backend-typecheck:
	cd backend && uv run mypy src

migrate:
	cd backend && uv run alembic upgrade head

api:
	cd backend && uv run uvicorn personal_travel.main:app --reload --port 8000

frontend-install:
	cd frontend && corepack pnpm install

frontend-check:
	cd frontend && corepack pnpm lint && corepack pnpm typecheck

web:
	cd frontend && corepack pnpm dev
