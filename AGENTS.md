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

Phases 1–5 are delivered and independently reviewed locally. The Phase 6
identity decision, secure-source lifecycle, booking
extraction, reservation review/confirmation, and review UI are implemented as
a locally reviewed implementation. Whole-phase review and coordinator
verification are closed. Private source ingress and
extraction default off and remain unavailable in local auth mode. Google OAuth
and Cloud Run IAM remain unprovisioned external gates. Phase 5 closure is recorded in
[`docs/releases/phase-5-local-proposals.md`](docs/releases/phase-5-local-proposals.md);
Phase 6 scope and limits are in
[`docs/releases/phase-6-booking-imports.md`](docs/releases/phase-6-booking-imports.md).
Phase 7 has an initial locally implemented, default-off source comparison
slice. It is not a whole-phase exit: category fixtures and live provider
approval are unverified, and date, price, availability, accessibility, and
memory retrieval remain unsupported. See
[`docs/releases/phase-7-travel-comparison.md`](docs/releases/phase-7-travel-comparison.md).

Current delivered capabilities include:

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
- A separately gated typed Phase 7 domain comparison for food, activity,
  neighborhood, and day-trip place leads; category and radius checks run in
  Travel, evidence expires, and saving or proposal handoff is explicit.
- Atomic manual place-plus-trip-candidate creation; AI results never mutate
  authoritative trip state.
- Monotonic trip/shared-place revisions, optional `X-Expected-Revision`
  preconditions on existing writes, and stale-write 409 recovery in the UI.
- Phase 5 implementation: accepted `itinerary-proposal-v1` integration with travel-owned durable
  lifecycle, stable-key recovery, immutable deterministic preview, atomic
  apply/replay, explicit rejection, and accessible responsive review UI.
- Phase 0–4 audit remediation: migration `0005` repairs legacy moved-item dates,
  SQL order/coordinate integrity, local host/origin guards, bounded external
  deadlines, database readiness and migrated disposable-schema test fixtures.
- Phase 6: migrations `0010`–`0013` add verified identity and owner-scoped
  source/import records. Authenticated streamed text/PDF intake, opaque local
  storage, bounded parsing, safe download/deletion, typed extraction, durable
  recovery, atomic reviewed reservation confirmation, and the review UI are
  implemented behind off-by-default gates. No real private input was used.
- Docker Compose Postgres for local development.
- CI skeleton.
- product/design/architecture/implementation/release documentation.

The travel-side research, comparison, and proposal gates default off; upstream
capability, storage, identity, and provider gates must be configured separately.
The comparison contract sends only search terms and the selected place's
coordinates; it does not fetch memory or confirm hours, prices, access, or
availability. The upstream
clock-domain fix passes local fake HTTP verification with Uvicorn `auto`
(uvloop on this host) and `asyncio`. Proposal gates remain off by default after independent local review. Migration `0008` adds exact upstream revision and
operation-support provenance; migration `0009` adds verified identity,
opaque sessions, OAuth attempts, and migration audit. Google mode derives each
domain owner from the server-verified Google issuer and subject; explicit local
mode remains available and unauthenticated. No domain route trusts browser
owner headers. Local-to-verified-owner migration is explicit, backed up, and
never triggered by first login. Phase 6 extraction, confirmation, and review
UI are independently reviewed and verified locally. Live private input,
cloud deployment, and general attachments remain gated or planned.

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
Detailed plans for Phases 5–9 are under
`docs/phase-5-implementation-plan.md` through `docs/phase-9-implementation-plan.md`.
Phase 5 and Phase 6 are independently reviewed locally. Phase 7's initial
consumer slice is implemented, with its complete exit gate still open. External
enablement gates remain closed. Phase 8–9 remain planned; stop before Phase 8
unless the user explicitly requests later work. Start that work from its
contract and dependency gates; documentation alone does not authorize cloud
deployment.
