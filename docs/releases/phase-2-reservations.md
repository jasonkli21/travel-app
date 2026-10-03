# Phase 2 release — reservations and saved places

Date: 2026-10-02  
Status: delivered locally  
Scope: manual reservation anchors, itinerary links, deterministic conflicts,
richer places, and trip-scoped saved-place candidates

## Delivered

- Additive PostgreSQL/Alembic schema for `reservations`, `saved_places`, richer
  place metadata, and nullable itinerary-item reservation links.
- Owner- and trip-scoped reservation CRUD with tentative, confirmed, and
  cancelled states; local trip-time schedule conversion; same-trip place/item
  validation; and explicit deletion behavior.
- Deterministic conflict indicators for non-cancelled scheduled reservations
  and itinerary items, excluding the intentionally linked anchor and
  boundary-only equality. Warnings are advisory and never auto-apply changes.
- Owner-scoped saved-place candidate relationships with duplicate protection,
  notes, removal without deleting the reusable place, and richer manual
  category/phone/website metadata.
- Typed API routes for reservations, saved places, place edits, and item
  reservation links, using the existing error envelope and same owner seam.
- Responsive trip overview/reservations workspace with status distinction,
  conflict details, item linking, saved candidates, and pending/error states.

Maps, provider search, external booking/email/calendar import, AI research or
proposals, authentication, attachments, background jobs, cloud deployment,
and live booking evidence remain deferred.

## Commits

1. `e442f6e` — `docs: plan Phase 2 reservations and saved places`
2. `ff38a38` — `feat: add Phase 2 reservation and saved-place model`
3. `08fc044` — `feat: expose Phase 2 reservation workflows`
4. `4e03360` — `feat: build Phase 2 reservation workspace`
5. This commit — `docs: record Phase 2 reservation release`
6. The independent-review follow-up commit will record review findings and
   fixes after Luna Max completes its read-only review.

## Verification performed

Backend, from the repository root with an isolated uv cache:

- `uv run --directory backend --locked pytest -q` — 16 passed, 7 skipped
  because PostgreSQL-backed tests require `TEST_DATABASE_URL`; one existing
  Starlette/httpx deprecation warning.
- `DATABASE_URL=... TEST_DATABASE_URL=... uv run --directory backend --locked
  pytest -q tests/test_phase1_postgres.py tests/test_phase2_postgres.py` — 7
  passed against isolated PostgreSQL 16; one existing Starlette/httpx
  deprecation warning.
- `uv run --directory backend --locked ruff check src tests` — passed.
- `uv run --directory backend --locked ruff format --check src tests` — passed.
- `uv run --directory backend --locked mypy src` — passed.
- Alembic upgrade to `head`, downgrade to `0002`, and upgrade to `head` again
  on a fresh isolated PostgreSQL database — passed.
- Phase 2 contract tests cover complete local schedule pairs, atomic schedule
  patch behavior, coordinate-update rules, and richer place metadata.

Frontend, from `frontend/`, using the repository-installed dependencies and
bundled Node runtime:

- ESLint — passed.
- `next typegen` and `tsc --noEmit` — passed.
- `node --test scripts/proxy-response.test.mjs` — 2 passed.
- `next build` — passed; routes remain `/`, `/trips/[tripId]`, `/api/health`,
  and `/api/v1/[...path]`.

The `pnpm` wrapper was not used for the final frontend checks because this
environment lacks the `corepack` command and the available pnpm attempted a
registry metadata fetch before aborting on a noninteractive modules purge. The
same repository-installed lint, typecheck, proxy-test, and build entrypoints
ran successfully directly.

## Remaining verification gaps

- Docker/Compose image builds and the documented container path were not run.
- Hosted CI, Cloud Run, Neon, production browser smoke testing, and external
  credentials were not used.
- No external booking/provider evidence was required; all Phase 2 records are
  manually entered and locally authoritative.
- The independent Luna Max review and any resulting fixes are recorded in the
  follow-up commit after this release commit.
