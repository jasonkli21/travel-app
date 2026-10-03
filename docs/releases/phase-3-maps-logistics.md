# Phase 3 release — maps and travel logistics

Date: 2026-10-03
Status: delivered locally; independent review completed
Scope: trip map, submitted place search/import, and on-demand itinerary logistics

## Delivered

- A responsive trip map using Geoapify `osm-carto` tiles, with day filtering,
  markers, route lines, a missing-key message, and an accessible location list.
- Owner-scoped, submitted geocoding search and explicit import to a reusable
  place and trip candidate. Repeat imports preserve user edits and candidate
  notes.
- Source name, exact attribution, license, and source URL retained with each
  imported provider place through reversible Alembic migration `0004`.
- On-demand walking, driving, bicycle, and transit route estimates for
  consecutive scheduled itinerary items with coordinates. Transfer warnings
  compare the available schedule gap with estimated duration plus a selectable
  buffer. Route data is returned to the UI and not persisted.
- Graceful no-key and provider-error behavior. Manual planning remains usable
  without Geoapify credentials.

## Commits

1. `7c1ca22` — `docs: plan Phase 3 maps and logistics`
2. `862d96b` — `feat: add Geoapify location and logistics API`
3. `cac5b46` — `feat: add trip map and logistics workspace`
4. `e548c30` — `docs: record Phase 3 maps and logistics release`
5. Review-fixes commit — stale logistics results, concurrent provider imports,
   and plan verification scope.

The implementation updates the initial plan and ADR 0007 with the final
dependency-free map renderer and source-attribution migration.

## Verification performed

Backend, using the repository's installed virtual environment directly:

- `backend/.venv/bin/pytest` — 25 passed, 14 skipped, one existing
  Starlette/httpx deprecation warning. PostgreSQL-backed Phase 1–3 tests were
  skipped because `TEST_DATABASE_URL` was not configured. This includes the
  new concurrent cross-trip provider-import regression test.
- `backend/.venv/bin/ruff check backend` and
  `backend/.venv/bin/ruff format --check backend` — passed.
- `backend/.venv/bin/mypy backend/src` — passed.
- `cd backend && .venv/bin/alembic history` — `0004` is the head revision.
  The test suite's Alembic config test also rendered an offline upgrade to
  head. Online upgrade/downgrade against PostgreSQL was not run.

Frontend, using installed `node_modules` and the bundled Node runtime directly:

- ESLint — passed.
- `next typegen` and `tsc --noEmit` — passed.
- `node --test scripts/*.test.mjs` — 5 passed, covering proxy responses,
  coordinate projection, date-line fitting, and tile wrapping.
- `next build` — passed; current routes include `/`, `/trips/[tripId]`,
  `/api/health`, and `/api/v1/[...path]`.

The local `uv run` cache path was not readable in the sandbox, and the `pnpm`
wrapper attempted dependency reconciliation without an interactive terminal.
No dependency or lockfile changes were needed; the existing virtualenv and
frontend executables were used for the checks above.

## External credentials and provider checks

No Geoapify keys were configured. Provider request/response behavior was
verified with mocked HTTP responses, but live geocoding, routing, and tile
requests were not exercised. Configure the server and browser keys as described
in [`07-local-development.md`](../07-local-development.md). Official Geoapify
pricing, map, geocoding, and routing documentation was checked on 2026-10-03;
the [pricing page](https://www.geoapify.com/pricing/) currently lists a free
allowance of 3,000 credits per day. Recheck provider limits and terms before a
public deployment or increased usage.

## Independent review

An independent Luna Max agent reviewed the implementation plan and code.
It reported three gaps:

- Logistics controls allowed day, mode, or buffer changes while a request was
  pending. The controls are now disabled until the request finishes, preventing
  an estimate from appearing under changed selections.
- Concurrent imports of the same provider place into different trips could
  race at the owner/provider/place unique constraint. The service now rolls
  back, reloads the winning place, and creates the other trip's saved-place
  link. A PostgreSQL-backed regression test forces both lookups to miss before
  either insert.
- The plan described UI-state automated tests that the frontend toolchain did
  not contain. The plan now distinguishes backend and map-geometry tests from
  UI integration verified by code review, ESLint, TypeScript, and production
  build.

The reviewer also checked the official Geoapify Routing API documentation and
confirmed the transit mode and multi-leg geometry match the client contracts.
Its targeted pre-fix checks reported 8 passed and 3 PostgreSQL-backed tests
skipped. After the fixes, the full available suite and frontend checks passed;
the new concurrency regression remains unexecuted locally because
`TEST_DATABASE_URL` is not configured.
