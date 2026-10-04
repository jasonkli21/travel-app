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

Phase 4 bounded AI research is delivered locally. **Phase 5 has only local
P5.0–P5.2 groundwork and remains incomplete**; see
[`docs/releases/phase-5-groundwork.md`](docs/releases/phase-5-groundwork.md).

The delivered scaffold and Phase 1–4 implementations include:

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
- A gated, typed `research-v1` integration with bounded active-day context,
  validated server-consumed SSE, cited results, and a research panel.
- Atomic manual place-plus-trip-candidate creation; AI results never mutate
  authoritative trip state.
- Monotonic trip/shared-place revisions, optional `X-Expected-Revision`
  preconditions on existing writes, and stale-write 409 recovery in the UI.
- Internal bounded typed proposal DTOs and immutable deterministic preview
  validation; these are not exposed as an AI or travel API contract.
- Phase 0–4 audit remediation: migration `0005` repairs legacy moved-item dates,
  SQL order/coordinate integrity, local host/origin guards, bounded external
  deadlines, database readiness and migrated disposable-schema test fixtures.
- Docker Compose Postgres for local development.
- CI skeleton.
- product/design/architecture/implementation/release documentation.

The travel-side AI research gate defaults off and external AI/provider gates
must be configured separately. No accepted upstream itinerary-proposal
capability, generation route/client, proposal storage, apply/reject lifecycle,
or proposal UI exists. Do not claim Phase 5 complete or later phases delivered
because they appear in planning docs. External booking imports, AI proposals,
authentication, cloud deployment, and attachments remain planned.

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
The comprehensive current review and verification are recorded in
`docs/reviews/phase-0-4-audit.md`. PostgreSQL checks require `TEST_DATABASE_URL`;
CI must execute them without skips. Migration `0005` requires online inspection
and a backup before legacy data repair.
Detailed plans for unimplemented Phases 5–9 are under
`docs/phase-5-implementation-plan.md` through `docs/phase-9-implementation-plan.md`.
Start later feature work from its contract/dependency gates; documentation
alone does not mark a capability implemented or authorize cloud deployment.
