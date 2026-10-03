# Agent instructions

This repository is intended for iterative work with Codex and human review.

## Read before changing code

For a new session, read in this order:

1. `README.md`
2. `docs/01-product-brief.md`
3. `docs/03-architecture.md`
4. `docs/04-data-model.md`
5. `docs/09-implementation-plan.md`
6. `docs/10-codex-handoff.md`
7. relevant ADRs under `docs/decisions/`

When working on cloud infrastructure, also read `docs/05-technology-choices.md` and `docs/08-cloud-deployment.md`.
When working on AI integration, also read `docs/06-ai-integration.md`.

## Current implementation status

The repository is at **Phase 3 — maps and travel logistics delivered locally**.

The delivered scaffold and Phase 1–3 implementations include:

- Next.js frontend shell.
- FastAPI application and health endpoint.
- PostgreSQL/SQLAlchemy/Alembic foundation.
- Initial travel-domain tables.
- Owner-scoped trip, day, item, and manual-place CRUD services and API routes.
- Deterministic inclusive trip-day reconciliation and transactional item ordering.
- Responsive manual itinerary UI and a typed same-origin API proxy.
- `PersonalAIClient` HTTP boundary.
- Owner-scoped manual reservations with tentative/confirmed/cancelled states,
  trip-local scheduling, itinerary links, and deterministic conflict indicators.
- Trip-scoped saved-place candidates and richer manual place metadata.
- Geoapify-backed submitted place search/import with persisted source attribution.
- A responsive trip map and on-demand route estimates with deterministic
  transfer warnings.
- Docker Compose Postgres for local development.
- CI skeleton.
- product/design/architecture/implementation/release documentation.

Do not claim later phases are implemented because they appear in planning docs.
External booking imports, AI research/proposals, authentication, cloud
deployment, and attachments remain planned rather than implemented.

## Architectural invariants

1. **Travel owns authoritative travel state.** Trips, itinerary items, reservations, user edits, saved places, and attachments belong to this application.
2. **AI is an external capability.** Access `personal-ai-system` through a typed HTTP client. Do not import its Python packages into this repository.
3. **SQL first.** Preserve relational integrity with PostgreSQL, foreign keys, constraints, transactions, and migrations.
4. **Portable SQL by default.** Avoid unnecessary PostgreSQL extensions, PL/pgSQL, triggers, and database-specific features in core tables. This keeps an eventual Aurora DSQL path credible.
5. **Hard constraints are deterministic.** Dates, budgets, availability flags, required fields, ordering invariants, and authorization are application code/database responsibilities—not model judgments.
6. **AI output does not silently mutate state.** Future AI-generated changes must be typed, validated, previewable, and explicitly applied through travel-domain services.
7. **Evidence and app state are different.** Current external observations such as hotel prices or opening hours are not permanent facts.
8. **Local mode remains first-class.** Cloud deployment must not make local PostgreSQL development a second-class path.
9. **No sensitive integrations before authentication.** Real email ingestion, booking imports, and private cloud data require an authenticated boundary.
10. **No premature platform framework.** Do not build generic plugin systems, message buses, or workflow engines until a concrete product phase requires them.

## Coding conventions

### Backend

- Python 3.12+.
- FastAPI/Pydantic for HTTP contracts.
- SQLAlchemy 2 typed ORM.
- Alembic for every schema change.
- `ruff` for lint/format.
- `mypy` for static typing.
- `pytest` for tests.
- Prefer services/repositories over provider/database calls inside route handlers.

### Frontend

- TypeScript strict mode.
- Next.js App Router.
- Keep domain data access behind client modules/hooks rather than calling ad hoc URLs throughout components.
- Do not add a large state-management library until server/client state requirements justify it.
- Prefer accessible, responsive components.

## Pull-request discipline

Each implementation PR should:

- target one phase/task;
- update tests;
- update docs if behavior/contracts changed;
- add an ADR for a meaningful architecture decision;
- call out what was verified locally vs. what still requires external credentials/deployment;
- avoid unrelated refactors.

## Commands

```bash
make db-up
make backend-install
make migrate
make backend-test
make backend-lint
make backend-typecheck

make frontend-install
make frontend-check
```

See `docs/07-local-development.md` for full setup. Verification performed during bootstrap is recorded in `docs/releases/phase-0-scaffold.md`.
