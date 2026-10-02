# Local development

Status: scaffold instructions  
Date: 2026-10-02

## Prerequisites

- Docker + Docker Compose
- Python 3.12+
- `uv`
- Node.js 22+
- `pnpm` via Corepack or local installation

## Environment

From repository root:

```bash
cp .env.example .env
```

Defaults assume:

```text
travel-api:          http://localhost:8000
travel-web:          http://localhost:3000
personal-ai-system:  http://localhost:8001
postgres:            localhost:5432
```

The AI system is optional for the initial travel CRUD phases.

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

## Tests/checks

```bash
make backend-test
make backend-lint
make backend-typecheck
make frontend-check
```

## Database migrations

Never mutate schema manually as the implementation source of truth.

After changing SQLAlchemy models:

```bash
cd backend
uv run alembic revision --autogenerate -m "describe change"
```

Review generated migration manually.

Then:

```bash
uv run alembic upgrade head
```

## Reset local DB

Destructive:

```bash
docker compose down -v
docker compose up -d postgres
cd backend
uv run alembic upgrade head
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
