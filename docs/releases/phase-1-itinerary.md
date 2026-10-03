# Phase 1 release — manual itinerary planner

Date: 2026-10-02  
Status: delivered locally  
Scope: manual trips, generated days, itinerary items, ordering, and manual places

## Delivered

- Owner-scoped trip list/detail/create/update/delete API.
- Inclusive one-day-per-date generation with transactional date-range
  reconciliation and nonempty-day shrink conflicts.
- Day-title editing, itinerary item CRUD, contiguous ordering, and atomic
  cross-day moves.
- Manual place creation/listing and owner-validated item attachments.
- Local `HH:MM` item times interpreted in the trip IANA timezone, with
  date-range, cross-midnight, DST-gap, and DST-fold validation.
- Typed FastAPI contracts and error envelopes.
- Explicit OpenAPI error/local-time response documentation and PostgreSQL-backed
  service/API regression coverage, including concurrent item appends.
- Same-origin Next.js API proxy, typed browser client, trips list, responsive
  trip workspace, accessible reorder/move controls, loading/error/empty states,
  manual-first AI boundary messaging, and no-content delete proxy handling.

AI research, proposals, reservations, maps, attachments, authentication,
cloud deployment, and provider integrations remain deferred.

## Commits

1. `4857d6e` — `docs: plan Phase 1 itinerary vertical slice`
2. `f7f4b68` — `feat: add trip and itinerary API`
3. `478f922` — `feat: build responsive manual itinerary planner`
4. `97965e8` — `docs: record Phase 1 itinerary release`
5. `05285dd` — `fix: address Phase 1 review findings`

## Verification performed

Backend, from `backend/`:

- `TEST_DATABASE_URL=... DATABASE_URL=... uv run --locked pytest -q` — 17
  passed against isolated PostgreSQL; one existing Starlette/httpx deprecation
  warning.
- `uv run --locked ruff check src tests` — passed.
- `uv run --locked ruff format --check src tests` — passed.
- `uv run --locked mypy src` — passed.
- Alembic `upgrade head`, `downgrade 0001`, and `upgrade head` against an
  isolated PostgreSQL 16.15 database — passed.
- FastAPI smoke flows against that database — passed for trip creation,
  inclusive day generation/expansion, item time round-trip, place attachment,
  cross-day move, item deletion, stable day removal, and nonempty-day `409`
  protection.
- PostgreSQL-backed Phase 1 integration tests — passed for front expansion and
  shrink reindexing, rollback on nonempty-day removal, ownership scoping, item
  CRUD/move/place behavior, OpenAPI response documentation, and concurrent
  item appends.

Frontend, from `frontend/`:

- ESLint — passed.
- `next typegen` and `tsc --noEmit` — passed.
- `next build` — passed; routes include `/`, `/trips/[tripId]`, `/api/health`,
  and `/api/v1/[...path]`.
- `node --test scripts/proxy-response.test.mjs` — passed for `204` no-content
  and JSON upstream bodies.

## Remaining verification gaps

- Docker/Compose was not available in the verification environment, so the
  documented container path and Docker image builds remain unverified here.
- Hosted CI, Cloud Run, Neon, and production browser smoke testing were not
  run and require their respective environments or credentials.
- No external credentials or `personal-ai-system` availability were required;
  its client remains health-only.
