# Personal Travel App

A local-first personal travel planning application with a rich itinerary UI and an explicit integration boundary to `personal-ai-system`.

The application owns authoritative travel state. `personal-ai-system` owns reusable AI capabilities such as research, memory, evidence-grounded synthesis, and—later—structured extraction/action proposals.

## Current status

**Phase 3 — maps and travel logistics delivered locally.** The repository has
the Phase 1 owner-scoped itinerary planner, Phase 2 manual reservations and
saved-place candidates, and a Phase 3 trip map, submitted place search/import,
and on-demand route estimates with deterministic transfer warnings. Geoapify
features are optional; manual planning works without provider keys. AI
research, external booking imports, authentication, and cloud deployment
remain planned.

## Stack

- **Web:** Next.js + React + TypeScript
- **API:** Python + FastAPI + Pydantic
- **ORM:** SQLAlchemy 2
- **Migrations:** Alembic
- **Local database:** PostgreSQL 16 via Docker Compose
- **Initial cloud compute:** Google Cloud Run
- **Initial cloud database:** Neon Postgres free tier
- **Future blob storage:** Google Cloud Storage
- **AI:** HTTP integration with `personal-ai-system`
- **Tooling:** `uv`, `pnpm`, `pytest`, `ruff`, `mypy`, TypeScript/ESLint, GitHub Actions

## Architecture

```text
                        personal-travel-app

              +-------------------------------+
              | Next.js / React / TypeScript  |
              | itinerary / places / bookings |
              | map / location search / routes |
              +---------------+---------------+
                              |
                              v
                    +-------------------+
                    | FastAPI API       |
                    | domain services   |
                    +----+----------+---+
                         |          |
             authoritative state   | typed HTTP
                         |          |
                         v          v
                    PostgreSQL   personal-ai-system
                                    |
                                    +-- research
                                    +-- memory
                                    +-- evidence
                                    +-- models
```

## Quick start

Prerequisites:

- Python 3.12+
- `uv`
- Node.js 22+
- `pnpm` 10.17.1 (or Corepack)
- Docker / Docker Compose

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env.local
docker compose up -d postgres

cd backend
uv sync --locked
uv run --locked alembic upgrade head
uv run --locked uvicorn personal_travel.main:app --reload --port 8000

# second terminal, from the repository root
cd frontend
corepack pnpm install --frozen-lockfile
corepack pnpm dev
```

Then open `http://localhost:3000`.

The backend reads the root `.env`; Next.js reads `frontend/.env.local`.
Set `TRAVEL_API_URL` there when using a nondefault backend address. To enable
map tiles, set `NEXT_PUBLIC_GEOAPIFY_API_KEY` in `frontend/.env.local`; to
enable submitted place search and route estimates, set `GEOAPIFY_API_KEY` in
the root `.env`. Provider keys are optional and should be restricted in
Geoapify; the browser-visible tile key should be limited to the local web
origin.
Use [`docs/07-local-development.md`](docs/07-local-development.md) for production
build/start commands and lockfile maintenance.

Create a trip from the home page, open it, add a day title and itinerary items,
attach a manually created place, move items with the accessible controls, then
reload the page to verify PostgreSQL-backed persistence. Add a tentative or
confirmed reservation, link it to an itinerary item, and save optional place
candidates from the trip workspace. In the map section, you can search for a
place, explicitly save a result as a candidate, and request travel estimates
between scheduled places for one day. The API is available under `/v1`; the web
app reaches it through the same-origin `/api/v1/*` proxy.

## Documentation

Start with:

1. [`AGENTS.md`](AGENTS.md)
2. [`docs/01-product-brief.md`](docs/01-product-brief.md)
3. [`docs/02-product-design.md`](docs/02-product-design.md)
4. [`docs/03-architecture.md`](docs/03-architecture.md)
5. [`docs/04-data-model.md`](docs/04-data-model.md)
6. [`docs/05-technology-choices.md`](docs/05-technology-choices.md)
7. [`docs/06-ai-integration.md`](docs/06-ai-integration.md)
8. [`docs/07-local-development.md`](docs/07-local-development.md)
9. [`docs/08-cloud-deployment.md`](docs/08-cloud-deployment.md)
10. [`docs/09-implementation-plan.md`](docs/09-implementation-plan.md)
11. [`docs/10-codex-handoff.md`](docs/10-codex-handoff.md)

Architecture decisions are under [`docs/decisions/`](docs/decisions/).

## Guiding rule

> Build a strong travel application first. AI augments the application; it does not become the application's database, business-rule engine, or only user interface.
