.PHONY: db-up db-down backend-install backend-test backend-lint backend-typecheck migrate api frontend-install frontend-check web

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

backend-install:
	cd backend && uv sync --locked

backend-test:
	cd backend && uv run --locked pytest

backend-lint:
	cd backend && uv run --locked ruff check . && uv run --locked ruff format --check .

backend-typecheck:
	cd backend && uv run --locked mypy src

migrate:
	cd backend && uv run --locked alembic upgrade head

api:
	cd backend && uv run --locked uvicorn personal_travel.main:app --reload --port 8000

frontend-install:
	cd frontend && corepack pnpm install --frozen-lockfile

frontend-check:
	cd frontend && corepack pnpm lint && corepack pnpm typecheck && corepack pnpm test

web:
	cd frontend && corepack pnpm dev
