# Phase 0 scaffold verification record

Date: 2026-10-02

## Scope

This record distinguishes initial bootstrap checks from the subsequent Phase 0
review and corrections. It is not evidence that Phase 1 product behavior or cloud
deployment is implemented.

## Initial bootstrap checks (historical)

- All backend Python source files parse successfully.
- `personal_travel.main` imports successfully using the available environment.
- SQLAlchemy metadata loads all four initial tables.
- `pytest` backend scaffold tests pass: **2 passed**.
- Alembic can render the `0001` migration to PostgreSQL SQL in offline mode.
- ORM-generated PostgreSQL DDL and migration constraint names were manually compared after fixing an initial metadata circular import.
- Frontend `.ts`/`.tsx` files parse with the installed TypeScript compiler with no syntax diagnostics.

## Initial bootstrap gaps (historical)

The execution environment could not resolve package registries, so these remain explicit handoff checks:

- clean `uv sync` from PyPI,
- lockfile generation/update,
- clean `pnpm install`,
- Next.js build,
- ESLint/typecheck against installed frontend dependencies,
- Docker Compose PostgreSQL startup,
- online Alembic migration against PostgreSQL 16,
- Docker image builds,
- Neon connectivity,
- Cloud Run deployment.

The later verification below closes the dependency/build/local-database gaps;
Docker and cloud checks remain outstanding.

## Phase 0 corrections and verification — 2026-10-02

Tested revision: the Phase 0 correction working tree on
`codex/phase-0-scaffold-corrections`, committed alongside this record; parent
revision `0920c360eb00da563313db415cf810a87f1ec0ca`.

Environment: Python 3.12.14, uv 0.11.13, Node 22.23.3, pnpm 10.17.1, and a
temporary PostgreSQL 16.15 cluster. Package downloads and local PostgreSQL/HTTP
servers required execution outside the sandbox. No existing database was reset.

### Corrections

- Fixed the typed AI URL default, backend lint/format failures, and Alembic
  handling of percent-encoded database passwords.
- Added mocked HTTP coverage for the health-only AI client; malformed JSON and
  response shapes now raise `PersonalAIError` like transport failures.
- Committed the dependency resolutions from the scaffold review as lockfiles,
  without changing dependency ranges. Make, CI, and Docker use locked installs.
- Documented the backend root `.env` and frontend `.env.local` separately.
  Added a frontend environment example containing the server-only backend URL.
- Aligned Next.js TypeScript configuration and type generation, ignored
  incremental output, and added frontend Docker-context exclusions.
- Added a local standalone launcher that copies static assets and loads frontend
  environment configuration in memory. The proxy normalizes trailing slashes and
  bounds backend requests to five seconds.
- Added PostgreSQL migration/parity and frontend production-build steps to CI.
  These steps were inspected locally; hosted CI has not been executed here.

No Phase 1 endpoints, travel services, schema changes, AI feature integration, or
cloud deployment were added. AST comparisons confirmed that the initial
migration and itinerary-model edits were formatting-only. No new ADR is needed
for these bootstrap corrections; the Phase 1 product/time/deletion/ordering
decisions remain open in the implementation plan and handoff.

### Executed checks

From the repository root, with the versions above on `PATH` and a temporary uv
cache:

| Command/check | Result |
| --- | --- |
| `make backend-install` (`uv sync --locked`) | Passed |
| `make backend-test` | 9 passed; no database or AI credentials required |
| `make backend-lint` | Ruff lint and formatting passed |
| `make backend-typecheck` | Passed for all 20 source files |
| `uv lock --check --project backend` | Passed |
| `uv build --project backend --out-dir <temporary-directory>` | Wheel and source distribution built |
| `make frontend-install` (pinned pnpm frozen install) | Passed |
| `make frontend-check` | ESLint, Next route type generation, TypeScript passed |
| `corepack pnpm build` from `frontend/` | Production build passed on Node 22 |
| `corepack pnpm start` from `frontend/` | Standalone server started; homepage and real backend proxy returned 200 |
| `git diff --check` and repository Markdown link review | Passed |

With `DATABASE_URL` pointing only to the temporary PostgreSQL cluster:

- `make migrate`, then `uv run --locked alembic check`,
  `uv run --locked alembic downgrade base`, `uv run --locked alembic upgrade head`,
  and another parity check from `backend/`: passed.
- SQLAlchemy metadata comparison reported no differences. Check-constraint names
  matched between ORM and the migrated database.
- Rolled-back fixtures verified ORM defaults, owner-scoped trip reads, trip/day/
  item cascades, place deletion setting references to null, and rejection of
  invalid trip dates, duplicate day dates/indexes, invalid item types/statuses,
  and reversed item times.
- Out-of-range/negative-index days and cross-owner place links are still accepted
  by the schema. Phase 1 services must enforce those documented invariants; this
  correction does not implement them.
- A temporary Uvicorn server returned the expected `/health` JSON over HTTP.

Standalone HTTP fixtures also verified frontend `.env.local` loading without
copying environment files into the build, shell-variable precedence, trailing-
slash normalization, static asset serving, unreachable-backend 503, and stalled-
backend 503 after approximately five seconds. Fixtures and servers were stopped.
Separate launcher fixtures verified conventional SIGINT/SIGTERM exit codes
(130/143) after forwarding the signal to the child process.

### Remaining gaps and warnings

- Docker is unavailable: Compose startup and both image builds remain unverified.
  Native PostgreSQL and standalone-server checks are not Docker build evidence.
- Hosted GitHub Actions, real `personal-ai-system`, Neon, and Cloud Run were not
  exercised. Cloud and AI feature integration remain deferred.
- The locked Starlette/TestClient emits an HTTPX deprecation warning; all tests
  pass. The frozen pnpm install warns that the `unrs-resolver` build script was
  ignored; lint, typecheck, and production build pass without enabling it.
- Registry access initially failed inside the sandbox; approved network retries
  succeeded. No dependency upgrades or additional provider credentials were used.
