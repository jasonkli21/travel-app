# Phase 1 implementation plan — manual itinerary planner

**Status:** execution plan for the first product vertical slice  
**Date:** 2026-10-02  
**Roadmap:** `docs/09-implementation-plan.md`  
**Baseline:** Phase 0 scaffold on `codex/phase-0-scaffold-corrections`

This plan turns the Phase 1 roadmap into a reviewable API, persistence, and UI
backlog. It follows the task-level structure and acceptance criteria used by the
Phase 1 plans in `../personal-ai-system/docs/phase-1-implementation-plan.md` and
`../personal-finance/docs/stage-1-implementation-plan.md`. Their domain-specific
choices do not apply to travel; the travel repository's product brief,
architecture, data model, and ADRs remain authoritative.

## Scope boundary

Phase 1 delivers a local, single-owner manual itinerary planner:

- create, list, open, edit, and permanently delete trips;
- automatically generate one trip day per inclusive calendar date;
- create, edit, delete, reorder, and move itinerary items across days;
- create owner-scoped manual places and optionally attach them to items;
- persist all authoritative state in the existing PostgreSQL schema;
- provide a typed FastAPI contract and a responsive Next.js trip workspace;
- keep the planner usable without `personal-ai-system` availability.

The phase does not add reservations, saved places, maps/geocoding, attachments,
AI research or proposals, authentication, collaboration, background jobs, cloud
deployment, or a provider-specific place integration. The existing AI health
client remains health-only.

## Current baseline and reference lessons

The Phase 0 scaffold already has the four relational tables, Alembic, an
owner-scoped trip repository example, the FastAPI health route, and a visual
placeholder in Next.js. It has no product CRUD, domain services, contracts,
travel API proxy, or authoritative itinerary UI.

The sibling plans are useful here for their dependency maps, narrow vertical
slices, route/record contract tables, acceptance criteria, failure matrices, and
release evidence. This plan keeps those practices while respecting this app's
SQL-first ownership model and avoiding AI/provider work that is explicitly out
of scope.

## Decisions that close the Phase 1 ambiguities

| Concern | Phase 1 decision |
| --- | --- |
| Identity | Read `owner_id` from server settings (`local` by default); never accept it from request bodies. Scope every trip, place, day, and item operation through its owning trip and configured owner. This remains a local identity seam, not authentication. |
| Trip days | Generate a day for every date from `start_date` through `end_date`, inclusive. `day_index` is one-based and follows date order. Day IDs and titles survive edits for dates that remain in range. |
| Trip date edits | Reconcile days in the same transaction as the trip edit. Add newly covered dates. Remove out-of-range days only when empty; reject the edit with `409 trip_days_contain_items` if a removed day contains items. Preserve overlapping day IDs, titles, and items, then renumber all day indexes. |
| Time semantics | Item times are local wall-clock times on the owning trip day, interpreted in the trip's IANA timezone. API inputs and outputs use `HH:MM` local times; the existing timezone-aware database columns store the corresponding instants. Reject invalid/nonexistent/ambiguous DST times and cross-midnight ranges. Date-only items keep both time values null. |
| Timezone edits | A trip timezone edit preserves each timed item's local date and wall-clock time by translating its stored instant from the old zone to the new zone. Reject the update if a value cannot be represented unambiguously or an item no longer fits its day. |
| Ordering | Each day has contiguous zero-based `sort_order` values. Create appends; delete compacts; move removes the item from its old day and inserts it at a zero-based destination index. Reindex both affected lists inside one database transaction. Disable overlapping UI mutations while a write is pending. |
| Destructive behavior | Item delete and trip delete are permanent. Trip delete relies on existing FK cascades for days/items; place deletion is not exposed in Phase 1 and its existing `SET NULL` behavior remains. The UI requires explicit confirmation before deleting a trip. |
| Places | Places are manually created, owner-scoped records. No external identity, map provider, or geocoding is required. An item may reference a place only when it belongs to the configured owner; foreign or missing IDs resolve as not found. |
| API errors | Domain and request-validation failures use `{ "error": { "code": string, "message": string, "details": object | null } }`. Not-found responses do not reveal whether a record belongs to a different owner. |

These choices implement the existing travel ADRs: PostgreSQL remains authoritative,
`owner_id = "local"` remains explicitly unauthenticated, and AI/maps/blob
providers remain deferred. No additional architecture decision is required for
this scoped implementation.

## Contract and invariants

### Route contract

All travel API routes are under `/v1`. The web app calls the same paths through a
server-side `/api/v1/*` proxy so the browser does not need a database or AI
service URL.

| Method | Route | Behavior |
| --- | --- | --- |
| `POST` | `/v1/trips` | Create trip and generate its days. |
| `GET` | `/v1/trips` | List the configured owner's trips, ordered by start date then creation time. Return summary fields and day/item counts. |
| `GET` | `/v1/trips/{trip_id}` | Return trip, ordered days, ordered items, and attached place summaries. |
| `PATCH` | `/v1/trips/{trip_id}` | Edit title, date range, or IANA timezone under the reconciliation rules above. |
| `DELETE` | `/v1/trips/{trip_id}` | Permanently delete the trip and cascaded days/items. |
| `PATCH` | `/v1/trips/{trip_id}/days/{day_id}` | Edit an optional day title. |
| `POST` | `/v1/trips/{trip_id}/days/{day_id}/items` | Append an item to a day. |
| `PATCH` | `/v1/trips/{trip_id}/items/{item_id}` | Edit type, title, notes, local times, status, or optional place. |
| `DELETE` | `/v1/trips/{trip_id}/items/{item_id}` | Permanently delete an item and compact its day order. |
| `POST` | `/v1/trips/{trip_id}/items/{item_id}/move` | Move an item to a day and insertion index; reindex source and destination atomically. |
| `GET` | `/v1/places` | List the configured owner's manually created places for item selection. |
| `POST` | `/v1/places` | Create a manual place with name and optional address/coordinates. |

Trip and item IDs are UUIDs. Request/response bodies use Pydantic models; no
client can supply `owner_id`, database sort order, generated day IDs, or
server-owned timestamps. Item type and status values match the checked values in
the initial migration. Strings are trimmed and bounded to the existing column
limits; blank required names/titles are rejected. Coordinates must be within
latitude/longitude ranges and either both be present or both omitted.

### Persistence and service rules

- Keep route handlers as HTTP adapters. Put validation and mutation policy in
  trip, itinerary, and place services; keep SQLAlchemy statements in repository
  methods.
- A service method owns its transaction boundary for each mutation. Trip-day
  reconciliation, item mutation, and reindexing either commit together or roll
  back together.
- Every lookup includes the configured owner or is reached through an already
  owner-scoped trip. An item/day from another trip is indistinguishable from a
  missing resource.
- Validate `start_date <= end_date`, a valid IANA timezone, positive one-based
  day indexes, nonnegative zero-based item positions, legal item type/status,
  local-time conversion, and same-day time ranges in the service. Keep database
  constraints for date/time ordering and add positive/nonnegative index checks
  in an additive Alembic revision.
- `GET /v1/trips/{id}` returns days sorted by `day_index`, items by
  `sort_order`, and local wall-clock times derived with the trip timezone.
- A date-range expansion may create up to 366 consecutive days in Phase 1;
  reject larger ranges with a validation error to bound response and transaction
  size. Existing larger trip data, if any, must not be silently truncated.
- Place coordinates remain ordinary numeric columns. No PostGIS or provider
  adapter is introduced.

### Frontend slice

- Replace the hard-coded Thailand scaffold with a trips list and trip workspace.
- Add create/edit trip flows, trip open/navigation, and confirmed trip deletion.
- Render every generated day with its date/title and ordered items; show clear
  loading, empty, and recoverable error states.
- Add/edit/delete items with type, title, notes, status, optional start/end
  time, and optional existing or quick-created place.
- Provide keyboard-accessible move-up/move-down controls and a destination-day
  selector; do not require drag-and-drop.
- Use the selected trip's IANA timezone for the visible time labels and form
  values. Date-only items remain visibly untimed.
- Keep the itinerary readable on mobile with stacked navigation/content and
  full-width forms; retain the restrained warm visual style from the scaffold.
- Put HTTP calls and shared request/response types in a frontend client module.
  The UI must not construct ad hoc backend URLs in individual components.
- Remove the placeholder AI prompt as an interactive surface. A small
  non-interactive note may say AI research is planned, but the itinerary has no
  AI dependency.

## Dependency map

```text
P1.1 Contracts + persistence constraints
        │
        v
P1.2 Repositories + trip/day services ──> P1.3 Trip API
        │                                     │
        └──> P1.4 Item/place services + API <─┘
                                              │
P1.5 Web API proxy + typed client ────────────┤
                                              v
                                  P1.6 Trips and itinerary UI
                                              │
                                              v
                                  P1.7 Release docs and review
```

## Work packages

### P1.1 — Finalize contracts and schema constraints

**Dependencies:** none.  
**Files:** Pydantic schema modules, API docs, SQLAlchemy models, Alembic.

**Work:**

1. Define enum/literal contracts for item type and status, trip create/update,
   day, place, item, trip summary/detail, and error response.
2. Define local `HH:MM` item-time semantics and timezone conversion rules in the
   API contract. Reject DST gaps/folds with a stable validation code.
3. Add model-level `day_index >= 1` and `sort_order >= 0` constraints and a new
   Alembic revision; do not rewrite the already-applied Phase 0 migration.
4. Add FastAPI handlers for domain errors and request validation that serialize
   the common error envelope without returning SQL or exception details.
5. Keep OpenAPI examples aligned with the implemented request/response models.

**Acceptance criteria:** the OpenAPI schema expresses each route body/response,
all enum values, local-time meaning, error codes, and nullable fields; migration
history remains additive; ORM metadata and migration constraints agree.

### P1.2 — Implement trip, day, and place persistence/services

**Dependencies:** P1.1.  
**Files:** repositories, trip/place services, transaction/session wiring.

**Work:**

1. Extend the trip repository to support owner-scoped create/get/list/update/
   delete and eager retrieval of days/items/places.
2. Add a trip-day repository for lookup, inclusive range generation, range
   reconciliation, stable IDs/titles, and one-based date ordering.
3. Add a place repository/service for owner-scoped list/create and coordinate
   validation.
4. Implement trip create/list/get/update/delete and day-title operations.
5. On date changes, preserve overlapping days, reject removal of nonempty days,
   create new days, drop only empty out-of-range days, and reindex atomically.
6. On timezone changes, preserve local wall-clock values and fail atomically if
   a conversion becomes ambiguous, nonexistent, or inconsistent with a day.

**Acceptance criteria:** repeated trip reads are stable; generated dates are
inclusive and contiguous; range expansion adds only missing dates; range shrink
never loses itinerary items; owner mismatches return not found; deletion uses
the existing cascade; failed reconciliation leaves the original trip intact.

### P1.3 — Expose trip and day HTTP routes

**Dependencies:** P1.1, P1.2.  
**Files:** `api/routes/trips.py`, router registration, error handling.

**Work:**

1. Implement trip create/list/detail/update/delete endpoints and day-title edit.
2. Return trip summaries for list; return fully ordered nested detail for open.
3. Add the configured owner and SQLAlchemy session only through dependencies;
   keep routes free of SQL statements and transaction policy.
4. Map missing/foreign resources to 404, invalid payloads to 422, and
   nonempty-day shrink conflicts to 409 using the standard envelope.

**Acceptance criteria:** the endpoint surface exactly matches the contract;
malformed UUIDs and missing IDs return safe structured 4xx responses; the list
does not expose another owner; a created trip can be reopened with all days.

### P1.4 — Implement itinerary item mutation and move behavior

**Dependencies:** P1.2, P1.3.  
**Files:** itinerary repository/service, item schemas/routes.

**Work:**

1. Implement item create/update/delete scoped through trip and day.
2. Convert local wall times using the trip day date and timezone; persist aware
   instants in existing columns; reject values outside the day and unsupported
   cross-midnight ranges.
3. Validate attached places against the configured owner. Permit clearing a
   place with an explicit null.
4. Append new items and keep the persisted order contiguous after deletes.
5. Implement move to an existing destination day and zero-based insertion
   index. Validate both source/destination under the requested trip; reindex
   source and destination lists in one transaction.
6. Translate integrity or stale target conflicts into stable domain errors;
   never partially move an item.

**Acceptance criteria:** create/edit/delete preserve allowed fields and order;
date-only items round-trip as untimed; DST-invalid and cross-day times fail;
moving within one day and between days produces the requested order; cross-trip
and cross-owner IDs cannot mutate state; every move is atomic.

### P1.5 — Add the web API proxy and typed frontend client

**Dependencies:** P1.3.  
**Files:** Next.js API route, `frontend/lib` client/types, environment docs.

**Work:**

1. Add a catch-all `/api/v1/*` server route that forwards supported methods and
   request bodies to the configured `TRAVEL_API_URL`, bounds request time, and
   returns upstream status/body safely.
2. Preserve the existing health route and default local backend URL.
3. Define typed client methods for trips, places, trip/day/item edits, delete,
   and move. Centralize envelope parsing and safe error messages.
4. Keep backend URL server-only; do not expose owner settings or database access
   to browser code.

**Acceptance criteria:** browser code calls only same-origin `/api/v1` routes;
backend-down responses are actionable; successful bodies retain their API
types; API paths are not duplicated throughout components.

### P1.6 — Build the responsive trip and itinerary UI

**Dependencies:** P1.4, P1.5.  
**Files:** App Router pages, feature components, styles.

**Work:**

1. Add a trips home with create form, trip summaries, loading/error/empty states,
   and open action.
2. Add a trip route with title/date/timezone edit, day navigation, full itinerary,
   item create/edit/delete, day-title edit, and confirmed trip deletion.
3. Add item status/type/time/notes editing, optional place selection and manual
   quick-create, plus accessible reorder and cross-day move controls.
4. Disable the edited row/form while a mutation is pending, surface API errors
   near the action, and refresh the authoritative trip detail after success.
5. Make navigation and forms usable at desktop and mobile widths; preserve
   focus behavior after create/edit/delete and use labels/semantic buttons.
6. Remove placeholder itinerary content and interactive-looking AI controls.

**Acceptance criteria:** a local user can build, close/reopen, edit, and delete a
trip with multiple days and items; changes persist after reload; mobile layout
does not require horizontal scrolling for core actions; all item changes are
available without AI.

### P1.7 — Document the delivered slice and handoff

**Dependencies:** P1.1–P1.6.  
**Files:** README/docs, Phase 1 release record.

**Work:**

1. Update the root README, docs index, implementation roadmap, and Codex handoff
   to distinguish Phase 1 behavior from deferred roadmap items.
2. Add `docs/releases/phase-1-itinerary.md` with schema/API summary, local smoke
   path, commands actually run, commit references, and environment-specific
   limitations.
3. Keep external credentials, cloud deployment, maps, and AI integration out of
   the release record except to state that they were not part of this phase.

**Acceptance criteria:** a fresh developer can follow documented local setup,
apply migrations, start API and web, create and reopen a trip, and identify
exactly which checks were run for this release.

## Commit sequence

Make the plan itself a separate commit before product code. Then use three
reviewable implementation commits; keep each commit internally coherent and
avoid mixing later-phase scope:

| Commit | Contents |
| --- | --- |
| `docs: plan Phase 1 itinerary vertical slice` | This plan and docs index link. |
| `feat: add trip and itinerary API` | Additive migration, Pydantic contracts, domain errors, repositories/services, trip/day/place/item routes, and backend API documentation. |
| `feat: build responsive manual itinerary planner` | Same-origin API proxy, typed client, trips list/workspace, complete manual trip/item/place flows, and responsive/accessibility details. |
| `docs: record Phase 1 itinerary release` | README/roadmap/handoff status and release record with commit IDs and actual verification evidence. |

If implementation reveals a meaningful new architecture decision, split that
ADR into the relevant backend commit and update the plan. Do not create an ADR
for routine route names, local validation, or this plan's resolved product
policies.

## Verification matrix

| Area | Required cases | Evidence to record |
| --- | --- | --- |
| Schema/migration | Fresh upgrade, downgrade/upgrade where safe, ORM/migration parity, positive day index and nonnegative item order | Migration and metadata checks |
| Trips/days | Inclusive date generation, leap/day-boundary dates, expansion, empty shrink, shrink with items, timezone validation/conversion | Service/API checks against PostgreSQL |
| Itinerary | Create/read/update/delete, all type/status values, null fields, date-only item, time ordering, DST gap/fold, out-of-day time | Service/API checks against PostgreSQL |
| Ordering | Append, delete compaction, same-day reorder, cross-day move, invalid destination/index, rollback | Service/API checks against PostgreSQL |
| Ownership | Foreign trip/day/item/place IDs, hidden-resource 404 behavior, request cannot override owner | API checks with two owner fixtures |
| Place | Manual create/list, invalid coordinate pairs/ranges, attach/clear, foreign place rejection | Service/API checks against PostgreSQL |
| Frontend | Trip create/open/edit/delete, item create/edit/delete/move, place quick-create, reload persistence, empty/error states, keyboard operation, mobile layout | Type/lint/build checks and documented local browser smoke path |
| Scope | AI health remains optional; no research call; no cloud/provider credentials required | Code/config review |

The repository convention is to update backend and frontend tests with behavior.
The exact commands remain `make backend-test`, `make backend-lint`,
`make backend-typecheck`, `make frontend-check`, and `corepack pnpm build` from
`frontend/`. A real local PostgreSQL migration/API smoke path is needed for
database behavior. The release record must distinguish commands run from
commands not run; Docker/cloud/hosted CI are not implied by local checks.

## Phase 1 completion review

Phase 1 is complete when:

- all P1.1–P1.7 acceptance criteria are met;
- a local owner can create, reopen, and fully edit an itinerary without AI;
- database transactions and owner scoping protect every mutation;
- generated trip days match the trip date range and item order is deterministic;
- frontend calls only the typed travel API client through the same-origin proxy;
- documentation and the release record match what was actually delivered;
- deferred reservations, maps, AI research/proposals, attachments, auth, and
  cloud deployment remain unimplemented.
