# Codex handoff

Status: Phase 2 delivered handoff
Date: 2026-10-02

## Objective

The Phase 1 manual itinerary planner and Phase 2 reservations/saved-places
slice are implemented as small, testable local vertical slices. The next
implementation target is Phase 3 only after the Phase 2 workflow has been
reviewed in use.

Do not implement maps/provider search, booking/email import, AI research, or
cloud deployment yet.

## Read first

1. root `AGENTS.md`
2. `docs/01-product-brief.md`
3. `docs/02-product-design.md`
4. `docs/03-architecture.md`
5. `docs/04-data-model.md`
6. `docs/09-implementation-plan.md`
7. ADRs under `docs/decisions/`

Use `docs/06-ai-integration.md` only to preserve the boundary; Phase 2 did not
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

## Phase 1 and Phase 2 verification

The October 2 Phase 0 review and corrections are recorded in
[`releases/phase-0-scaffold.md`](releases/phase-0-scaffold.md). Phase 1 evidence,
commit references, and environment-specific limitations are recorded in
[`releases/phase-1-itinerary.md`](releases/phase-1-itinerary.md).
Phase 2 evidence, commit references, and environment-specific limitations are
recorded in [`releases/phase-2-reservations.md`](releases/phase-2-reservations.md).

The implementation plan is preserved in
[`phase-1-implementation-plan.md`](phase-1-implementation-plan.md), and the
Phase 2 plan is preserved in
[`phase-2-implementation-plan.md`](phase-2-implementation-plan.md). The
delivered route surface is:

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
- manual places are owner-scoped and no provider/map integration is introduced.

The Phase 2 service decisions are:

- reservation status distinguishes `tentative`, `confirmed`, and `cancelled`;
- reservation schedules use local date/time fields in the trip timezone and
  persist aware instants; DST gaps/folds fail and cross-midnight reservations
  are allowed;
- an itinerary item links to at most one same-trip reservation, while a
  reservation may anchor multiple items;
- saved places are unique trip/place candidate relationships and do not delete
  their reusable place;
- conflict warnings are deterministic and advisory, exclude linked anchors and
  cancelled records, and never mutate itinerary state.

## Things explicitly deferred

Do not add now:

- direct Gemini/OpenAI calls,
- memory tables,
- research evidence tables,
- a travel-owned search agent,
- Gmail connector,
- maps provider,
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

Phase 1 and Phase 2 are local-first.

Do not deploy or add Neon/GCP secrets as part of feature work unless separately authorized.

Preserve compatibility with the documented deployment path.

## Future handoff expectations

Record the exact commits reviewed, checks actually run, remaining local or
external verification gaps, and whether a later phase needs an ADR. Keep AI,
cloud, maps, external imports, and authentication scope explicitly separated
from the delivered Phase 2 slice.
