# Travel App

A full-stack personal travel planning application for building itineraries, managing reservations and places, organizing travel documents, researching destinations, and reviewing AI-assisted travel suggestions without giving the AI system authority over application state.

The project is designed as a local-first modular monolith with a Next.js web application, a FastAPI backend, PostgreSQL for authoritative travel data, and an explicit HTTP boundary to a separate `personal-ai-system`.

## What it does

Travel App combines traditional travel-planning workflows with optional AI-assisted features.

Core functionality includes:

- day-by-day trip and itinerary planning;
- reservations and tentative travel options;
- saved places and trip-place relationships;
- map-based place search and travel/logistics estimates;
- source-backed travel research;
- reviewed AI itinerary proposals with explicit preview/apply;
- booking/document import with typed extraction and confirmation;
- travel comparisons for supported place categories;
- private trip attachments;
- static HTML, ICS, and versioned JSON exports;
- a read-focused travel view for use during a trip.

AI features are deliberately separated from authoritative application state. Research and generation can propose or summarize information, but travel state changes remain explicit, validated application operations.

## Design principles

- **Travel data is authoritative in PostgreSQL.**
- **AI output is advisory until explicitly reviewed and applied.**
- **External provider calls are bounded, typed, and optional.**
- **Private data features fail closed when authentication or storage requirements are not met.**
- **Local development does not require cloud credentials or AI/provider access.**
- **The system stays a modular monolith unless a concrete operational requirement justifies additional infrastructure.**

## Architecture

```text
                         Browser
                            |
                            v
                +-----------------------+
                | Next.js / React       |
                | UI + same-origin API  |
                | proxy                 |
                +-----------+-----------+
                            |
                            v
                +-----------------------+
                | FastAPI travel API    |
                |                       |
                | domain services       |
                | auth / validation     |
                | transactions          |
                | provider orchestration|
                +----+-------------+----+
                     |             |
                     |             | typed HTTP
                     v             v
                PostgreSQL    personal-ai-system
                                   |
                                   +-- research
                                   +-- evidence
                                   +-- proposals
                                   +-- extraction
                                   +-- domain lookup

Private local documents
        |
        v
opaque filesystem store
```

### Application boundaries

**Frontend**

- renders the product UI;
- owns interaction and transient browser state;
- calls the travel API through the same-origin `/api/v1/*` proxy;
- does not access PostgreSQL or mutate AI-system state directly.

**Travel API**

- owns travel-domain validation and invariants;
- owns authentication and owner scoping;
- owns database transactions and revision checks;
- coordinates optional map/AI providers;
- validates and applies reviewed AI proposals.

**PostgreSQL**

- is the authoritative store for structured travel state;
- enforces relational constraints while service-layer transactions enforce aggregate invariants.

**`personal-ai-system`**

- is an external capability boundary;
- owns reusable AI/research/model functionality;
- does not replace the travel application's database or business rules.

**Private source store**

- stores local private document bytes by opaque key outside the served application tree;
- is intentionally separate from PostgreSQL.

## Repository layout

```text
.
├── backend/
│   ├── src/personal_travel/
│   │   ├── api/            # FastAPI routes, schemas, middleware
│   │   ├── auth/           # Google OIDC, sessions, owner migration
│   │   ├── clients/        # Geoapify and personal-ai-system clients
│   │   ├── db/             # SQLAlchemy engine/session setup
│   │   ├── domain/         # shared domain/value types and contracts
│   │   ├── models/         # SQLAlchemy models
│   │   ├── repositories/   # persistence operations
│   │   └── services/       # domain workflows and transaction ownership
│   ├── migrations/         # Alembic migrations
│   ├── scripts/            # operational/backup utilities
│   └── tests/
├── frontend/
│   ├── app/                # Next.js App Router pages/routes
│   ├── components/         # UI and workflow components
│   ├── lib/                # typed API/proxy/client utilities
│   ├── scripts/
│   └── tests/
├── docs/                   # architecture, decisions, runbooks, detailed design
├── infrastructure/         # deployment/infrastructure notes
├── docker-compose.yml      # local PostgreSQL
└── README.md
```

## Tech stack

### Web

- Next.js
- React
- TypeScript
- pnpm

### API

- Python 3.12+
- FastAPI
- Pydantic
- SQLAlchemy 2
- Alembic
- psycopg

### Data and deployment

- PostgreSQL locally
- Neon Postgres as the initial hosted database target
- Google Cloud Run as the initial hosted compute target
- optional Google OIDC for hosted/private-data access

### Tooling

- `uv`
- pytest
- Ruff
- mypy
- ESLint
- TypeScript
- GitHub Actions
- Docker

## Local setup

### Prerequisites

Install:

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- Node.js 22+
- pnpm
- Docker / Docker Compose

Optional features may additionally require credentials for Google OAuth, Geoapify, or a running `personal-ai-system`.

### 1. Clone and configure

```bash
git clone https://github.com/jasonkli21/travel-app.git
cd travel-app

cp .env.example .env
cp frontend/.env.example frontend/.env.local
```

The checked-in example configuration defaults to local development with AI and private-file features disabled.

### 2. Start PostgreSQL

```bash
docker compose up -d postgres
```

### 3. Install and start the backend

```bash
cd backend

uv sync --locked
uv run --locked alembic upgrade head
uv run --locked uvicorn personal_travel.main:app --reload --port 8000 --no-access-log
```

Useful endpoints:

```text
GET http://localhost:8000/health
GET http://localhost:8000/ready
```

### 4. Install and start the frontend

In another terminal:

```bash
cd frontend

corepack enable
pnpm install --frozen-lockfile
pnpm dev
```

Open:

```text
http://localhost:3000
```

The default server-side backend URL is:

```text
TRAVEL_API_URL=http://localhost:8000
```

## Optional local integrations

### Geoapify

Server-side place search and routing use:

```env
GEOAPIFY_API_KEY=...
```

Browser map tiles use a separate browser-visible key:

```env
NEXT_PUBLIC_GEOAPIFY_API_KEY=...
```

### `personal-ai-system`

Set the backend service URL:

```env
PERSONAL_AI_BASE_URL=http://localhost:8001
```

Individual AI capabilities are independently gated and should only be enabled when the upstream service contract is configured.

### Private imports and attachments

Private file features require verified hosted identity and an absolute private source directory outside the repository.

Do not place private source files under a publicly served directory.

## Development checks

### Backend

```bash
cd backend

uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy src
uv run --locked pytest
uv build
```

### Database migrations

```bash
cd backend

uv run --locked alembic upgrade head
uv run --locked alembic check
```

### Frontend

```bash
cd frontend

pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

## Production containers

Build from the repository root:

```bash
docker build -t travel-api ./backend
docker build -t travel-web ./frontend
```

The API and web containers run as non-root users and expect runtime configuration through environment variables rather than baked credentials.

## Cloud deployment

The initial hosted topology is:

```text
Browser
   |
   v
Cloud Run: travel-web
   |
   v
Cloud Run: travel-api
   | \
   |  +---- HTTPS ----> personal-ai-system
   |
   +------ TLS -------> Neon Postgres
```

The repository provides production containers and hosted-mode configuration, but cloud provisioning remains operator-managed.

### Cloud prerequisites

Provision:

1. a Neon PostgreSQL database;
2. a Google Cloud project with Cloud Run;
3. a Google OAuth web client;
4. Secret Manager entries for server-side secrets;
5. the `personal-ai-system` endpoint/identity configuration if AI features will be enabled.

### Hosted backend configuration

Typical hosted configuration includes:

```env
TRAVEL_DEPLOYMENT_MODE=hosted
TRAVEL_AUTH_MODE=google_oidc

DATABASE_URL=postgresql+psycopg://...

GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
GOOGLE_OAUTH_REDIRECT_URI=https://<web-host>/auth/google/callback

ALLOWED_HOSTS=<api-host>
CORS_ORIGINS=https://<web-host>
```

Run Alembic as an explicit release step rather than from every API replica.

### Hosted web configuration

```env
TRAVEL_DEPLOYMENT_MODE=hosted
TRAVEL_API_URL=https://<travel-api-service>
TRAVEL_WEB_ALLOWED_HOSTS=<web-host>
```

### Private files in cloud environments

The current private document store is a hardened local-filesystem implementation.

Do **not** enable hosted private imports or attachments using ephemeral Cloud Run storage. A durable shared blob backend must be implemented and verified first.

## Backup and recovery

The backend includes encrypted PostgreSQL/private-store backup tooling using `age`.

From `backend/`:

```bash
uv run python scripts/secure_backup.py backup \
  --store-dir /absolute/path/to/private-store \
  --output-dir /absolute/path/to/encrypted-backups \
  --recipient age1... \
  --confirm-writes-stopped
```

Verify an artifact before relying on it:

```bash
uv run python scripts/secure_backup.py verify \
  --artifact /absolute/path/to/encrypted-backups/travel-....tar.age \
  --identity /secure/path/travel-age-identity.txt
```

See the operations runbooks under `docs/runbooks/` for restore procedures.

## Security notes

- local identity is a development seam, not production authentication;
- hosted mode requires verified Google identity;
- private features fail closed rather than silently falling back;
- provider keys and OAuth/database credentials remain server-side;
- untrusted provider and document inputs are size/deadline bounded;
- private file names are not used as filesystem paths;
- AI results are validated before they can affect authoritative travel state.

Keep `.env`, `.env.local`, private source directories, backup identities, database URLs, and cloud/provider credentials out of version control.

## Documentation

Detailed architecture, data-model, deployment, ADR, implementation, and operations material lives under `docs/`.

Useful starting points include:

```text
README.md
docs/03-architecture.md
docs/04-data-model.md
docs/07-local-development.md
docs/08-cloud-deployment.md
```

## License

No license is currently specified. Add an explicit `LICENSE` file before treating the repository as generally reusable open-source software.
