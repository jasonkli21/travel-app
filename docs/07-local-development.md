# Local development

Status: Phases 1–5 plus the review-pending Phase 6 identity foundation
Date: 2026-10-04

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

Production builds bake the public tile key into browser assets: rebuild after
changing it (Docker build argument `NEXT_PUBLIC_GEOAPIFY_API_KEY`). Setting it
only at container start does not change the browser bundle.

Development defaults bind web and PostgreSQL to loopback. The API accepts
`ALLOWED_HOSTS=localhost,127.0.0.1,::1` and browser origins in `CORS_ORIGINS`
(localhost/127.0.0.1 port 3000 by default). For a different browser port, update
that list if calling the API directly. The web proxy validates its browser
Host against `TRAVEL_WEB_ALLOWED_HOSTS` and accepts only the same browser
origin; forwarded host headers are not trusted. Clients without Origin are
still unauthenticated. Do not expose this local owner on a public interface.

## Identity modes and local-owner migration

`TRAVEL_AUTH_MODE=local` is the default and preserves unauthenticated local
CRUD. Invalid browser credentials never switch local requests into another
identity mode. Google sign-in is optional and requires a server-side OAuth web
client, a one-person `GOOGLE_OAUTH_ALLOWED_EMAIL`, client ID/secret, and an
exact callback URI ending in `/auth/google/callback`. Set the same client ID
and callback URI in `frontend/.env.local`; only the client ID and redirect URI
are exposed to the Next server, never to browser code. Use HTTPS except for
explicit loopback development, and keep `ALLOWED_HOSTS`, `CORS_ORIGINS`, and
`TRAVEL_WEB_ALLOWED_HOSTS` aligned with the actual local URL. Do not enable
Google mode until those credentials and redirect settings are provisioned and
tested. The local identity tests use synthetic RSA-signed fixtures instead of
live Google credentials.

Research/proposal use in Google mode also requires the independent
`PERSONAL_AI_AUTH_MODE=google_cloud_run_iam` gate, with the user token audience
matching the OAuth client, plus the configured Cloud Run service audience and
service-account identity. No live IAM binding or upstream audience alignment
is included in local verification. Keep AI feature gates off unless the full
service boundary is separately configured.

Migration `0009` adds identities and sessions; it does not claim old rows on
first sign-in. To move a local owner, provision/verify the target by a Google
sign-in, create and independently verify a database backup outside the
repository, then inspect a dry-run report:

```bash
cd backend
uv run --locked python scripts/migrate_local_owner.py --target-owner-id '<verified-owner-id>'
```

Apply only the exact inspected plan, after checking the graph counts and
conflicts and independently verifying the backup SHA-256. Preserve the emitted
`run_id` and `plan_digest` and pass them back with a literal source/target
confirmation and backup path/digest using `--apply`. A changed graph, provider
or idempotency collision, invalid owner relation, changed target identity, or
bad backup fails closed. The command is operator-driven and was verified only
against disposable synthetic PostgreSQL data; it has not been run on real user
data.

API/proxy request bodies are limited to 64 KiB. Database defaults bound connect
time to 5 seconds, pool acquisition to 5 seconds, statements to 15 seconds and
lock waits to 5 seconds. The settings in `.env.example` are explicit. External
research has one 45-second deadline (maximum 50); logistics provider work has
a 30-second deadline and at most 50 eligible transfers. Errors expose request
IDs and safe envelopes; request logs omit user text and query strings.
The supplied API commands disable Uvicorn access logging because its default
records include full query strings. Keep `--no-access-log` when launching it
manually; application logs record safe route templates instead.

The AI system and Geoapify are optional. Manual itinerary and reservation
flows continue to work without either service.

AI research is disabled by default. To turn on the travel-side endpoint, set
`PERSONAL_AI_RESEARCH_ENABLED=true` and `PERSONAL_AI_BASE_URL` in the root
`.env`. `PERSONAL_AI_TIMEOUT_SECONDS` defaults to 45 seconds. The separate
`personal-ai-system` backend must also enable its research gate. Its search
provider and storage settings are independent; use that repository's guide for
real provider setup and rights gates.

For an end-to-end local demonstration without provider credentials, configure
the AI backend with `RESEARCH_ENABLED=true`, `RESEARCH_STORAGE=memory`, and
`RESEARCH_SEARCH_ADAPTER=fake`, then enable `PERSONAL_AI_RESEARCH_ENABLED` for
Travel. Fake results are explicitly synthetic and are not verified real-world
information. Memory storage is process-local and loses its research sessions
on restart. Real search-provider behavior is not implied by this setup.

### Itinerary proposals (local fake only)

Proposal generation is separately gated at the browser, travel API, and AI
service. All gates default off. To show the proposal panel, set
`NEXT_PUBLIC_TRAVEL_PROPOSALS_ENABLED=true` in `frontend/.env.local`, set
`PERSONAL_AI_PROPOSALS_ENABLED=true` and `PERSONAL_AI_BASE_URL` in the root
`.env`, and run the upstream backend locally with
`ITINERARY_PROPOSALS_ENABLED=true`, `ITINERARY_PROPOSAL_GENERATOR=fake`, and
`ITINERARY_PROPOSAL_STORAGE=memory`. The fake generator and process-local store
use no provider credentials; its output is synthetic. Keep the provider gate
off. The UI sends context-only requests and does not attach research sessions.

When `uvloop` is installed, run the local upstream fake with Uvicorn's asyncio
loop while this upstream bug is open:

```bash
cd /path/to/personal-ai-system/backend
ITINERARY_PROPOSALS_ENABLED=true \
ITINERARY_PROPOSAL_GENERATOR=fake \
ITINERARY_PROPOSAL_STORAGE=memory \
uv run uvicorn personal_ai.main:app --host 127.0.0.1 --port 8001 --loop asyncio --no-access-log
```

On this host, Uvicorn auto-selects uvloop 0.23.0, whose `loop.time()` differs
from `time.monotonic()` by about 11.16 million seconds. The accepted upstream
proposal code passes that loop-based absolute deadline into synchronous code
that compares it with `time.monotonic()`, so the fake endpoint returns
`generation_outcome_unknown` immediately under uvloop. Keep the default-off
gates off in that runtime until the upstream deadline conversion is fixed.
Travel contract, lifecycle, and UI tests remain local and credential-free.

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

`/health` is process liveness. `curl http://localhost:8000/ready` also checks
database availability and returns a safe `503` when storage is unavailable.

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

Run the complete database suite against local PostgreSQL:

```bash
TEST_DATABASE_URL=postgresql+psycopg://travel:travel@localhost:5432/travel make backend-test
```

The supplied test account needs schema-creation privileges. Fixtures create
UUID-named disposable schemas, apply real migrations, check ORM parity, use
production session settings and remove only those schemas. Use an isolated
test database; do not point tests at private/production data. Without
`TEST_DATABASE_URL`, SQL tests are explicitly skipped and the run is not
complete Phase 0–4 verification. CI sets it and runs all SQL and frontend Node
tests. Frontend tests live under `frontend/tests/` and are part of
`make frontend-check`.

Run `corepack pnpm build` from `frontend/` as well when changing its build or
runtime setup. Offline backend checks do not require real AI/Geoapify services.
PostgreSQL-backed Phase 1–4 tests and online migration checks require
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

Phase 4 adds no frontend package dependency, database migration, or travel-side
research-session table. Migration `0005` repairs legacy schedule/order data
and adds SQL integrity. Phase 5 groundwork adds migration `0006` with
nonnegative trip/place revisions; it is additive and initializes existing
rows to zero. Migration `0007` adds owner-scoped proposal persistence with
trip-delete cascade; `0008` adds exact upstream revision and operation-support
provenance, backfilling existing proposal rows. Travel clients consume versioned
AI HTTP contracts and do not share its Python packages. Proposal
generation/apply always require trip revisions; existing manual writes retain
optional legacy preconditions.

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

Then run `uv run --locked alembic check` to verify ORM/migration parity.
Migration `0005` requires an online connection; offline SQL can be generated
through `0004`, but is not a complete upgrade to head. Migration `0009` is the
current head.

## Backup and migration recovery

Before upgrading an existing database, stop API writes and create a local
backup outside the repository. For the default Docker database:

```bash
docker compose exec -T postgres pg_dump -U travel -d travel -Fc > /tmp/travel-before-upgrade.dump
cd backend
uv run --locked alembic upgrade head
uv run --locked alembic check
```

Keep the dump somewhere durable/private if it is needed beyond this session.
Migration `0005` preserves local times while repairing legacy source-date
timestamps and normalizes order before adding uniqueness. Invalid coordinate
pairs/ranges or a destination DST gap/fold abort the whole migration. Inspect
the affected records using an authorized local SQL session, correct their
schedule/coordinates explicitly, and retry; do not guess an ambiguous instant.
The upgrade expects application writes to be stopped throughout inspection.

Downgrade removes the new constraints but cannot reconstruct repaired data.
To recover the old state, restore the backup into a separate database and
verify it before switching `DATABASE_URL`:

```bash
docker compose exec -T postgres createdb -U travel travel_restore
docker compose exec -T postgres pg_restore -U travel -d travel_restore --no-owner < /tmp/travel-before-upgrade.dump
```

Inspect `alembic current` and representative trips against that restored
database. Do not run the corrected code against an old schema; either correct
legacy records and upgrade the restored database or run the matching prior
application revision. Hosted backup/restore drills remain Phase 9 work.

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
