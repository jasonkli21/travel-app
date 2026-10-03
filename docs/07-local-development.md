# Local development

Status: Phase 3 local instructions
Date: 2026-10-03

## Prerequisites

- Docker + Docker Compose
- Python 3.12+
- `uv`
- Node.js 22+
- `pnpm` 10.17.1 via Corepack or local installation

## Environment

From repository root:

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env.local
```

Defaults assume:

```text
travel-api:          http://localhost:8000
travel-web:          http://localhost:3000
personal-ai-system:  http://localhost:8001
postgres:            localhost:5432
```

The backend loads the root `.env` when launched from the repository root or
`backend/`. Next.js loads `frontend/.env.local`, not the root `.env`.
Set `TRAVEL_API_URL` in `frontend/.env.local` to change the proxy's backend address;
it remains server-only. Environment variables supplied by the shell/container
take precedence over environment files.

Geoapify features are optional. To enable submitted place search and route
estimates, set `GEOAPIFY_API_KEY` in the root `.env`; this key is read only by
the backend. To enable map tiles, set `NEXT_PUBLIC_GEOAPIFY_API_KEY` in
`frontend/.env.local`. The tile key is visible in browser requests, so restrict
it to the local web origin and tile API in Geoapify. Restart the web process
after changing the public tile key. Manual planning and coordinate entry work
without either key.

The AI system and Geoapify are optional. Manual itinerary and reservation
flows continue to work without either service.

## Start PostgreSQL

```bash
make db-up
```

Inspect:

```bash
docker compose ps
```

## Backend

```bash
make backend-install
make migrate
make api
```

Health:

```bash
curl http://localhost:8000/health
```

Expected:

```json
{"status":"ok","service":"travel-api"}
```

## Frontend

```bash
make frontend-install
make web
```

Open `http://localhost:3000`.

Proxy health: `http://localhost:3000/api/health`.

For a production-mode local smoke check:

```bash
cd frontend
corepack pnpm build
corepack pnpm start
```

The start script prepares static assets for the standalone server. Supply
`PORT` to change its listening port and `HOSTNAME` to change its bind address.
The proxy reports `503` when the backend is unreachable or does not respond
within its bounded timeout.

## Tests/checks

```bash
make backend-test
make backend-lint
make backend-typecheck
make frontend-check
```

Run `corepack pnpm build` from `frontend/` as well when changing its build or
runtime setup. Backend checks do not require a database or real AI/Geoapify
service. PostgreSQL-backed Phase 1–3 tests and online migration checks require
an isolated PostgreSQL 16 database. Provider-client tests use mocked HTTP
responses and do not require credentials.

## Dependency locks

Commit `backend/uv.lock` and `frontend/pnpm-lock.yaml` with intentional dependency
changes. Installation uses `uv sync --locked` and
`corepack pnpm install --frozen-lockfile`; CI and Docker consume the same locks.
To refresh a lock after an approved manifest change, run `uv lock` in `backend/`
or `corepack pnpm install --no-frozen-lockfile` in `frontend/`, review the lock diff,
and rerun the affected checks. Do not update dependencies as a side effect of
ordinary scaffold verification.

Phase 3 adds no frontend package dependency. It adds reversible Alembic
migration `0004` for source attribution fields on provider-imported places.

## Database migrations

Never mutate schema manually as the implementation source of truth.

After changing SQLAlchemy models:

```bash
cd backend
uv run --locked alembic revision --autogenerate -m "describe change"
```

Review generated migration manually.

Then:

```bash
uv run --locked alembic upgrade head
```

## Reset local DB

Destructive:

```bash
docker compose down -v
docker compose up -d postgres
cd backend
uv run --locked alembic upgrade head
```

## Running with local `personal-ai-system`

Configure:

```env
PERSONAL_AI_BASE_URL=http://localhost:<its-api-port>
```

Do not assume its internal package paths or database are available to this repository.

## Local data

Local PostgreSQL and future local uploads are intentionally independent from cloud data.

Automatic local/cloud synchronization is out of scope.
