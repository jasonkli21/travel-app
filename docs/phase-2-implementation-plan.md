# Phase 2 implementation plan — reservations and saved places

**Status:** Phase 2 plan approved for implementation  
**Date:** 2026-10-02  
**Roadmap:** [`09-implementation-plan.md`](09-implementation-plan.md)  
**Baseline:** Phase 1 delivered locally on `codex/phase-0-scaffold-corrections`

This plan turns the Phase 2 roadmap into a reviewable, local-first vertical
slice. It keeps the Phase 1 ownership, transaction, time, and API-boundary
rules authoritative and adds only the data needed to represent booked anchors
and optional trip candidates.

## Scope boundary

Phase 2 delivers:

- owner-scoped manual reservations belonging to a trip;
- explicit `tentative`, `confirmed`, and `cancelled` reservation states;
- optional links from itinerary items to reservations;
- deterministic reservation-versus-itinerary conflict indicators;
- richer manually maintained place metadata;
- trip-scoped saved-place records for optional candidates;
- a trip overview and reservations workspace in the existing responsive UI;
- local PostgreSQL migrations, typed FastAPI contracts, service tests, and
  frontend checks.

Phase 2 does not deliver:

- email, document, calendar, or booking-provider import;
- live availability, price, opening-hours, or travel-time observations;
- maps, geocoding, a selected map/place provider, or PostGIS;
- AI research, proposals, memory, or direct model calls;
- authentication, collaboration, background jobs, cloud deployment, blobs, or
  reservation monitoring.

Saved places are manually entered candidates. A saved place is not a booking,
does not imply that an itinerary item exists, and does not become authoritative
external evidence merely because it is stored in the travel database.

## Decisions that close the Phase 2 ambiguities

The architectural choices in this section are also recorded in
[`decisions/0006-phase2-reservations-and-saved-places.md`](decisions/0006-phase2-reservations-and-saved-places.md).

| Concern | Phase 2 decision |
| --- | --- |
| Identity and ownership | Reservations and saved-place rows carry `owner_id`. Every lookup is scoped to the configured server owner, and a reservation/place/item link is accepted only when all records belong to that owner and the same trip. `local` remains an unauthenticated bootstrap seam. |
| Reservation lifecycle | `tentative` means a planned but not confirmed booking, `confirmed` means a booked anchor, and `cancelled` preserves a manually recorded booking that is no longer active. Delete is permanent and explicit; deleting a reservation clears item links with `ON DELETE SET NULL`. |
| Reservation types | Use checked strings: `lodging`, `flight`, `train`, `car_rental`, `activity`, `dining`, and `other`. The list is deliberately small and can be extended by a later migration when product behavior requires it. |
| Reservation time | API callers submit optional local `start_date`/`start_time` and `end_date`/`end_time` fields in the owning trip's IANA timezone. A scheduled reservation must have a start date and time; its end is optional, but an end date requires an end time. Cross-midnight ranges are allowed. A reservation with all four fields null is unscheduled and cannot produce a time conflict. Persist scheduled values as timezone-aware instants in `starts_at`/`ends_at`; reject invalid, nonexistent, and ambiguous local times. When the trip timezone changes, preserve each reservation endpoint's local date and wall-clock value by re-resolving it in the new timezone, just as Phase 1 does for itinerary items; reject the trip edit if the new zone makes an endpoint invalid or the range impossible. |
| Itinerary link cardinality | `itinerary_items.reservation_id` is nullable. An item links to at most one reservation; one reservation may anchor multiple items. The item and reservation must belong to the same trip. This avoids an unnecessary association aggregate while preserving the useful one-booking-to-many-itinerary-items case. |
| Conflict semantics | For each non-cancelled reservation, compare its scheduled interval with non-cancelled scheduled itinerary items in the same trip. The linked item itself is excluded because that is the intended anchor. Positive-length intervals overlap when `a.start < b.end` and `b.start < a.end`; a single supplied endpoint is deliberately treated as a point event, while both endpoints missing means unscheduled; a point event overlaps a positive interval when it falls inside the interval. Boundary-only equality is not a conflict. Results are computed in application code and returned with stable IDs, day/item context, and a human-readable reason. |
| Saved places | `saved_places` is a first-class owner/trip/place join with one row per trip/place and an optional candidate note. The existing owner-scoped `places` record remains reusable across trips. Saving a place never changes the place or itinerary item and removing the saved relationship does not delete the place. |
| Place metadata | Add optional manually maintained `category`, `phone`, and `website_url` fields. No provider-specific lookup or normalization is introduced. Place edits remain owner-scoped and preserve the existing optional coordinates/provider identity fields. |
| API errors | Reuse the Phase 1 error envelope. Missing, foreign-owner, cross-trip, and invalid links are safe `404`/`422` responses; reservation deletion and conflict reporting do not expose another owner's records. |

## Persistence and contract design

### Database changes

Add one additive Alembic revision after the Phase 1 head:

```text
reservations
  id UUID PK
  owner_id
  trip_id FK -> trips ON DELETE CASCADE
  reservation_type
  status
  provider_name
  confirmation_code?
  starts_at?
  ends_at?
  place_id? FK -> places ON DELETE SET NULL
  source_reference?
  notes?
  created_at
  updated_at

saved_places
  id UUID PK
  owner_id
  trip_id FK -> trips ON DELETE CASCADE
  place_id FK -> places ON DELETE CASCADE
  note?
  created_at
  updated_at

itinerary_items.reservation_id? FK -> reservations ON DELETE SET NULL

places.category?
places.phone?
places.website_url?
```

Use ordinary PostgreSQL columns, checked strings, foreign keys, indexes, and a
unique `(owner_id, trip_id, place_id)` saved-place constraint. Keep reservation
provider-specific material out of an unvalidated JSON document. A later import
phase may add typed extraction metadata after its contract is accepted.

The migration must be reversible in a clean database. The ORM models,
relationships, indexes, foreign keys, and checked values must match the
migration without rewriting `0001` or `0002`.

### HTTP route contract

All routes remain under `/v1` and use the existing owner/session dependencies.

| Method | Route | Behavior |
| --- | --- | --- |
| `GET` | `/v1/trips/{trip_id}/reservations` | List the trip's reservations with local schedule fields, linked-item summaries, and deterministic conflicts. |
| `POST` | `/v1/trips/{trip_id}/reservations` | Create a manual reservation after validating owner, trip, place, schedule, and status/type. |
| `PATCH` | `/v1/trips/{trip_id}/reservations/{reservation_id}` | Edit reservation fields or clear its optional values; revalidate all schedule and link invariants. The four schedule fields are an atomic patch group: if any is present, all four must be present; four nulls clear the schedule. |
| `DELETE` | `/v1/trips/{trip_id}/reservations/{reservation_id}` | Permanently delete a reservation and clear its itinerary-item links. |
| `GET` | `/v1/trips/{trip_id}/saved-places` | List trip candidates with place metadata and saved notes. |
| `POST` | `/v1/trips/{trip_id}/saved-places` | Save an existing owner place to the trip with an optional note; reject duplicates deterministically. |
| `DELETE` | `/v1/trips/{trip_id}/saved-places/{saved_place_id}` | Remove the trip relationship without deleting the reusable place. |
| `PATCH` | `/v1/trips/{trip_id}/saved-places/{saved_place_id}` | Edit the optional candidate note without changing the place. |
| `PATCH` | `/v1/places/{place_id}` | Edit owner-scoped manual place metadata and optional coordinates. |

Extend the existing item create/update contracts with nullable
`reservation_id`. Trip detail item responses include a compact reservation
summary (`id`, type, status, provider, confirmation code, conflict count), but
the full reservation list stays on the dedicated reservation route. Place
responses include the new metadata. Saved-place and reservation response
schemas include server IDs/timestamps where they are useful for ordering and
editing; clients never provide owner or server timestamps.

### Service and repository rules

- Keep route handlers as HTTP adapters. Add reservation and saved-place
  repositories; do not put SQLAlchemy statements in routes.
- Every reservation mutation locks the trip aggregate root before reading or
  changing linked items. Item mutations that set or clear `reservation_id`
  use the same trip lock as other item mutations.
- Validate a reservation's place through the owner-scoped place repository and
  validate its item link through the owner-scoped trip aggregate. A resource
  from another owner or trip is indistinguishable from a missing resource.
- Put local reservation schedule parsing/conversion beside the Phase 1 time
  helpers. Preserve the trip-local date and wall-clock values on round-trip;
  do not reinterpret a reservation in the browser's timezone.
- Keep conflict calculation deterministic and side-effect free. It must not
  call `personal-ai-system`, provider APIs, or mutate itinerary state. Reuse a
  pure interval helper in both reservation serialization and item reservation
  summaries.
- Return reservations ordered by scheduled start (unscheduled last), then
  status/provider/creation time. Return saved places ordered by place name.
- Keep permanent reservation deletion explicit in the UI. A cancelled status
  remains available for manual history and is excluded from conflict warnings.

## Frontend slice

- Extend the typed API client with reservation, saved-place, and place-edit
  methods. Browser components call only this client through the existing
  same-origin proxy.
- Add an overview strip to the trip workspace with counts for itinerary items,
  confirmed/tentative reservations, saved candidates, and active conflicts.
- Add a reservation section with create/edit/delete flows, status/type badges,
  provider/confirmation/source fields, local schedule fields, optional place,
  notes, linked-item summaries, and visible conflict details.
- Add an optional reservation selector to the item form. Saving an item with a
  reservation ID links it atomically through the itinerary service; clearing
  the selector removes the link.
- Turn the existing places panel into a saved-place candidate surface. Allow
  saving/removing an existing place for this trip, editing the saved note, and
  manually creating/editing the richer place metadata. Do not imply maps or
  external provider data.
- Disable overlapping mutations, retain failed form values, show recoverable
  API errors near the action, and refresh authoritative trip/reservation/saved
  place data after success.
- Keep the existing responsive, accessible layout and manual-first AI boundary.

## Dependency map

```text
P2.1 model/contracts/migration
          │
          v
P2.2 repositories/services/conflicts ──> P2.3 reservation/saved-place API
                                                     │
P2.4 typed client/proxy -----------------------------┤
                                                     v
                                  P2.5 overview/reservation UI
                                                     │
                                                     v
                                      P2.6 docs/release evidence
```

## Work packages and acceptance criteria

### P2.1 — Add Phase 2 schema and typed contracts

**Files:** models, schemas, serializers, migration, schema/contract tests.

1. Add reservation and saved-place models/relationships and richer place
   columns; add `reservation_id` to itinerary items.
2. Add the additive migration with constraints, indexes, and reversible
   downgrade behavior.
3. Define literal reservation type/status contracts and local schedule request/
   response fields, including explicit nullable/omitted patch behavior.
4. Extend item and place response/request contracts and OpenAPI error
   documentation.

**Acceptance:** a fresh database and a Phase 1 database can upgrade to the same
head; ORM metadata matches the revision; invalid types/statuses, schedule
shapes, and place fields fail before persistence; item/reservation links are
nullable and safe on reservation deletion.

### P2.2 — Implement reservation, saved-place, and conflict services

**Files:** repositories, reservation/saved-place services, time/conflict
helpers, item/place service extensions, backend unit/integration tests.

1. Implement owner/trip-scoped reservation CRUD and saved-place create/list/
   delete with service-owned transaction boundaries.
2. Validate schedule values in the trip timezone, including DST gaps/folds,
   cross-midnight ranges, unscheduled records, and partial updates.
3. Implement reservation links on item create/update, including same-trip
   checks, clearing, and safe not-found behavior.
4. Implement conflict calculation and compact item reservation summaries.
5. Add PostgreSQL-backed tests for CRUD, ownership, cross-trip links, duplicate
   saved places, deletion cascades/SET NULL, time round-trips and timezone
   edits, conflict edge cases (one-sided points, boundary equality, cancelled
   items/reservations, unscheduled records, and multiple conflicts), and
   transaction rollback.

**Acceptance:** all writes are atomic and owner-scoped; linked items cannot
silently point across trips; cancelled reservations do not warn; intended
linked anchors do not warn themselves; true overlaps are reported with stable
context; deleting a reservation clears item links without deleting the item.

### P2.3 — Expose Phase 2 API routes

**Files:** reservation/saved-place/place routes, router registration, API
contract tests.

1. Add the reservation and saved-place routes from the route table.
2. Add place patch behavior and item reservation fields.
3. Serialize local schedules, place metadata, linked items, and conflict
   details using the common error envelope.
4. Keep route code free of SQL/query logic and preserve the existing `/health`
   and Phase 1 route behavior.

**Acceptance:** OpenAPI documents every new request/response and the 404/409/
422 errors; resource ownership is not leaked; list/detail responses are stable
after reload; Phase 1 API tests remain green.

### P2.4 — Extend the typed web client

**Files:** `frontend/lib/api.ts`, proxy tests only if proxy behavior changes.

1. Add TypeScript types and methods for reservations, saved places, richer
   places, and item reservation links.
2. Keep all calls same-origin and use the existing error parser.
3. Preserve no-content handling and do not add browser-side API URL logic.

**Acceptance:** strict TypeScript and proxy tests pass; all new methods match
the FastAPI contract; API errors remain actionable.

### P2.5 — Build the reservation/overview/saved-place UI

**Files:** trip workspace components, styles, frontend checks.

1. Fetch trip detail, owner places, trip reservations, and saved places as one
   refreshable workspace state.
2. Add overview counts and the reservation list/form with clear tentative vs.
   confirmed vs. cancelled presentation.
3. Add conflict callouts and linked itinerary-item context; never auto-mutate
   an item when merely displaying a warning.
4. Add item reservation selection and saved-place candidate actions.
5. Add manual place metadata editing/creation while retaining mobile and tablet
   access to the saved-place section, labels, keyboard operation,
   pending-state disabling, and error recovery.

**Acceptance:** a local user can create a tentative reservation, confirm it,
link it to an item, see it in the overview and reservation section, observe a
real conflict warning, cancel/delete it safely, save/remove a place candidate,
and reload with the same state. No AI or provider availability is required.

### P2.6 — Record delivery and handoff

**Files:** README, data model, roadmap, handoff, docs index, release record.

1. Mark only Phase 2 behavior as delivered and keep later phases deferred.
2. Update data-model tables, route summaries, the handoff, and the docs index.
3. Add `docs/releases/phase-2-reservations.md` with exact implementation and
   review commits, checks actually run, local smoke coverage, and remaining
   Docker/cloud/credential gaps.

**Acceptance:** a fresh developer can identify the Phase 2 schema/API/UI,
apply migrations, run local checks, understand conflict semantics, and see
that imports, maps, AI, auth, and cloud deployment remain out of scope.

## Commit sequence

The plan is committed before product code. Implementation commits group related
layers rather than mirroring every work-package bullet:

| Commit | Contents |
| --- | --- |
| `docs: plan Phase 2 reservations and saved places` | This plan, the Phase 2 ADR, and documentation index/roadmap links. |
| `feat: add Phase 2 reservation and saved-place model` | Additive migration, ORM relationships, typed contracts/serializers, richer place fields, and schema/contract tests. |
| `feat: expose Phase 2 reservation workflows` | Repositories, services, time/conflict logic, item-link behavior, API routes, and PostgreSQL/API regression coverage. |
| `feat: build Phase 2 reservation workspace` | Typed client, overview/reservation/saved-place UI, place metadata controls, and responsive/accessibility styling. |
| `docs: record Phase 2 reservation release` | Delivered-state docs, release evidence, and handoff updates. |
| `fix: address Phase 2 independent review findings` | Every actionable finding from the independent Luna Max review, with focused tests/docs. |

If implementation reveals a separate meaningful architecture decision, add or
amend an ADR in the relevant implementation commit. Do not create commits for
individual fields, endpoints, or test cases.

## Verification matrix

| Area | Required cases | Evidence |
| --- | --- | --- |
| Migration/schema | Upgrade from Phase 1, clean upgrade, downgrade/upgrade, ORM/migration parity, checked values, FK actions, unique saved place | Alembic and PostgreSQL checks |
| Reservations | Create/list/update/delete, status/type values, optional fields, unscheduled records, local schedule round-trip, cross-midnight, DST gap/fold, invalid patch shapes | Backend unit/API tests and PostgreSQL integration tests |
| Links/ownership | Link/clear item reservation, same-trip requirement, foreign owner/trip IDs, reservation delete `SET NULL`, trip delete cascade | API tests with multiple owners/trips |
| Saved places/places | Rich place metadata, edit ownership, save/list/remove, duplicate rejection, place reuse across trips, place deletion behavior if exposed | Backend/API tests |
| Conflicts | True overlap, boundary-only equality, point event, cancelled reservation/item, linked anchor exclusion, multiple conflicts, unscheduled no-warning | Pure helper and API response tests |
| Frontend | Overview counts, reservation CRUD/status, conflict rendering, item link/clear, saved candidate save/remove/note, rich place edit, reload persistence, pending/error/mobile behavior | Lint/typecheck/proxy/build and documented smoke path |
| Scope | No AI/provider/import/auth/cloud calls; existing health client remains unchanged | Code/config review |

The repository commands remain `make backend-test`, `make backend-lint`,
`make backend-typecheck`, `make frontend-check`, and `corepack pnpm build`
from `frontend/`. PostgreSQL-backed behavior should run with an isolated test
database when available; the release record must distinguish skipped tests and
environment gaps from passing checks.

## Phase 2 completion review

Phase 2 is complete when:

- P2.1–P2.6 acceptance criteria are met;
- a local owner can manage tentative and confirmed booking anchors without AI;
- optional saved places remain distinct from reservations and itinerary state;
- conflict warnings are deterministic, explainable, and non-mutating;
- all links, deletes, and schedule conversions preserve SQL and owner
  invariants;
- the frontend uses only the typed same-origin travel client;
- documentation and release evidence match the delivered commits and checks;
- maps, provider lookups, imports, AI, auth, attachments, background jobs, and
  cloud deployment remain unimplemented.
