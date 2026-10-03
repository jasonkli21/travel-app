# Codex handoff

Status: Phase 3 delivered handoff
Date: 2026-10-03

## Objective

The Phase 1 manual itinerary planner, Phase 2 reservations/saved-places slice,
and Phase 3 maps/location/logistics slice are implemented locally. Review
evidence and commit references are recorded in the phase release documents.

Phase 4 AI research is the next planned phase. Do not invent its external API
contract; booking/email import, authentication, and cloud deployment remain
deferred.

## Read first

1. root `AGENTS.md`
2. `docs/01-product-brief.md`
3. `docs/02-product-design.md`
4. `docs/03-architecture.md`
5. `docs/04-data-model.md`
6. `docs/09-implementation-plan.md`
7. ADRs under `docs/decisions/`

Use `docs/06-ai-integration.md` only to preserve the boundary; Phase 3 did not
expand the AI API.

## Current implementation contents

Backend:

- FastAPI app and `/health`.
- Pydantic settings.
- SQLAlchemy engine/session.
- `Trip`, `TripDay`, `Place`, `ItineraryItem`, `Reservation`, and `SavedPlace`
  models with additive Phase 1/2 migrations and relational constraints.
- owner-scoped repositories and services for trip/day/place/item operations.
- owner/trip-scoped reservation and saved-place services with place metadata
  editing, itinerary links, and deterministic conflict calculation.
- typed Geoapify HTTP client for submitted geocoding search and on-demand
  routing, plus owner-scoped result import and saved-candidate reuse.
- trip map with Geoapify raster tiles, an explicit missing-key state, marker
  and route geometry, and an accessible source-attributed location list.
- deterministic logistics eligibility and transfer warnings; route estimates
  remain response-only and do not mutate travel state.
- Alembic migration `0004` retains source attribution, license, and link for
  imported provider places.
- typed Pydantic request/response contracts and a common error envelope.
- deterministic inclusive day generation and date-range reconciliation.
- local-time item conversion with IANA timezone/DST validation.
- transactional contiguous ordering and cross-day move behavior.
- trip-root row locking for concurrent item-order mutations.
- `PersonalAIClient` with health only.
- pytest/ruff/mypy configuration.

Frontend:

- Next.js App Router trips list and trip workspace.
- same-origin `/api/v1/*` proxy and centralized typed client.
- responsive trip/day/item/place/reservation/candidate forms with loading,
  empty, and error states.
- accessible move-up/move-down and destination-day controls.
- trip overview counts, reservation status distinction, linked-item context,
  and visible conflict warnings.
- TypeScript/ESLint setup and production build verification.

Infrastructure:

- local PostgreSQL 16 through Docker Compose.
- Dockerfiles for API/web.
- GCP/Neon deployment documented but not implemented.

## Phase 1–3 verification

The October 2 Phase 0 review and corrections are recorded in
[`releases/phase-0-scaffold.md`](releases/phase-0-scaffold.md). Phase 1 evidence,
commit references, and environment-specific limitations are recorded in
[`releases/phase-1-itinerary.md`](releases/phase-1-itinerary.md).
Phase 2 evidence, commit references, and environment-specific limitations are
recorded in [`releases/phase-2-reservations.md`](releases/phase-2-reservations.md).
Phase 3 evidence, commit references, and provider/database limitations are in
[`releases/phase-3-maps-logistics.md`](releases/phase-3-maps-logistics.md).

The implementation plan is preserved in
[`phase-1-implementation-plan.md`](phase-1-implementation-plan.md), and the
Phase 2 plan is preserved in
[`phase-2-implementation-plan.md`](phase-2-implementation-plan.md). The Phase 3
plan and provider decision are in
[`phase-3-implementation-plan.md`](phase-3-implementation-plan.md) and
[`decisions/0007-geoapify-maps-and-logistics.md`](decisions/0007-geoapify-maps-and-logistics.md).
The delivered route surface is:

```text
POST   /v1/trips
GET    /v1/trips
GET    /v1/trips/{trip_id}
PATCH  /v1/trips/{trip_id}
DELETE /v1/trips/{trip_id}
PATCH  /v1/trips/{trip_id}/days/{day_id}

POST   /v1/trips/{trip_id}/days/{day_id}/items
PATCH  /v1/trips/{trip_id}/items/{item_id}
DELETE /v1/trips/{trip_id}/items/{item_id}
POST   /v1/trips/{trip_id}/items/{item_id}/move
GET    /v1/places
POST   /v1/places
PATCH  /v1/places/{place_id}
GET    /v1/trips/{trip_id}/reservations
POST   /v1/trips/{trip_id}/reservations
PATCH  /v1/trips/{trip_id}/reservations/{reservation_id}
DELETE /v1/trips/{trip_id}/reservations/{reservation_id}
GET    /v1/trips/{trip_id}/saved-places
POST   /v1/trips/{trip_id}/saved-places
PATCH  /v1/trips/{trip_id}/saved-places/{saved_place_id}
DELETE /v1/trips/{trip_id}/saved-places/{saved_place_id}
GET    /v1/trips/{trip_id}/places/search?q=...&limit=...
POST   /v1/trips/{trip_id}/saved-places/import
POST   /v1/trips/{trip_id}/logistics/estimate
```

The route -> service -> repository boundary is implemented and remains
intentionally lightweight; routes do not contain SQLAlchemy query logic.

The Phase 1 service decisions are:

- owner identity is read from server settings and never accepted from request bodies;
- days are inclusive, one-based, contiguous, and reconciled transactionally;
- item ordering is contiguous and zero-based within each day;
- local `HH:MM` values are interpreted in the trip timezone and DST gaps/folds fail;
- concurrent item mutations serialize on the trip aggregate root;
- deleting a trip or item is permanent; shrinking over a nonempty day returns `409`;
- manual places are owner-scoped; provider-imported places keep source
  attribution separately from user-editable place metadata.

The Phase 2 service decisions are:

- reservation status distinguishes `tentative`, `confirmed`, and `cancelled`;
- reservation schedules use local date/time fields in the trip timezone and
  persist aware instants; DST gaps/folds fail, cross-midnight reservations are
  allowed, and trip timezone edits preserve reservation wall-clock values;
- reservation schedule PATCHes treat the four local schedule fields as an
  atomic group; four nulls clear the schedule;
- an itinerary item links to at most one same-trip reservation, while a
  reservation may anchor multiple items;
- saved places are unique trip/place candidate relationships and do not delete
  their reusable place;
- conflict warnings are deterministic and advisory, exclude linked anchors and
  cancelled records, and never mutate itinerary state.

The Phase 3 service decisions are:

- Geoapify is the tile, geocoding, and routing provider; map tiles use an
  in-repository renderer with no map-package dependency.
- provider search is submitted explicitly and imports only after user action;
  re-import preserves user edits and candidate notes.
- exact source attribution is stored with imported places and displayed with
  search results, saved places, and the map location list.
- route estimates use only coordinates and the selected mode, are fetched on
  explicit request, and stay out of durable trip state.
- transfer warnings compare route time plus a selected buffer with the actual
  schedule gap. Missing coordinates or timestamps produce no route leg.

## Things explicitly deferred

Do not add now:

- direct Gemini/OpenAI calls,
- memory tables,
- research evidence tables,
- a travel-owned search agent,
- Gmail connector,
- external booking/email/calendar import,
- PostGIS,
- Redis,
- Pub/Sub,
- Celery,
- generic event bus,
- WebSockets,
- microservices,
- domain plugin framework,
- automatic AWS/GCP dual support,
- Kubernetes.

## Personal AI integration rule

Do not implement a made-up research endpoint.

The existing client is intentionally health-only until `personal-ai-system` exposes an accepted external contract.

Future integration remains HTTP, not Python package sharing.

## Cloud rule

Phases 1–3 are local-first.

Do not deploy or add Neon/GCP secrets as part of feature work unless separately authorized.

Preserve compatibility with the documented deployment path.

## Future handoff expectations

Record the exact commits reviewed, checks actually run, remaining local or
external verification gaps, and whether a later phase needs an ADR. Keep AI,
cloud, external imports, and authentication scope explicitly separated from
the delivered Phase 3 slice.
