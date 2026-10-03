# Codex handoff

Status: Phase 1 delivered handoff
Date: 2026-10-02

## Objective

The Phase 1 manual itinerary planner is implemented as a small, testable local
vertical slice. The next implementation target is Phase 2 only after the Phase
1 workflow has been reviewed in use.

Do not implement AI research, maps, email import, or cloud deployment yet.

## Read first

1. root `AGENTS.md`
2. `docs/01-product-brief.md`
3. `docs/02-product-design.md`
4. `docs/03-architecture.md`
5. `docs/04-data-model.md`
6. `docs/09-implementation-plan.md`
7. ADRs under `docs/decisions/`

Use `docs/06-ai-integration.md` only to preserve the boundary; Phase 1 should not expand the AI API.

## Current implementation contents

Backend:

- FastAPI app and `/health`.
- Pydantic settings.
- SQLAlchemy engine/session.
- `Trip`, `TripDay`, `Place`, and `ItineraryItem` models with additive ordering
  constraints in the Phase 1 migration.
- owner-scoped repositories and services for trip/day/place/item operations.
- typed Pydantic request/response contracts and a common error envelope.
- deterministic inclusive day generation and date-range reconciliation.
- local-time item conversion with IANA timezone/DST validation.
- transactional contiguous ordering and cross-day move behavior.
- `PersonalAIClient` with health only.
- pytest/ruff/mypy configuration.

Frontend:

- Next.js App Router trips list and trip workspace.
- same-origin `/api/v1/*` proxy and centralized typed client.
- responsive trip/day/item/place forms with loading, empty, and error states.
- accessible move-up/move-down and destination-day controls.
- TypeScript/ESLint setup and production build verification.

Infrastructure:

- local PostgreSQL 16 through Docker Compose.
- Dockerfiles for API/web.
- GCP/Neon deployment documented but not implemented.

## Phase 1 verification

The October 2 Phase 0 review and corrections are recorded in
[`releases/phase-0-scaffold.md`](releases/phase-0-scaffold.md). Phase 1 evidence,
commit references, and environment-specific limitations are recorded in
[`releases/phase-1-itinerary.md`](releases/phase-1-itinerary.md).

The implementation plan is preserved in
[`phase-1-implementation-plan.md`](phase-1-implementation-plan.md), and the
delivered route surface is:

```text
POST   /v1/trips
GET    /v1/trips
GET    /v1/trips/{trip_id}
PATCH  /v1/trips/{trip_id}
DELETE /v1/trips/{trip_id}

POST   /v1/trips/{trip_id}/days/{day_id}/items
PATCH  /v1/trips/{trip_id}/items/{item_id}
DELETE /v1/trips/{trip_id}/items/{item_id}
POST   /v1/trips/{trip_id}/items/{item_id}/move
GET    /v1/places
POST   /v1/places
```

The route -> service -> repository boundary is implemented and remains
intentionally lightweight; routes do not contain SQLAlchemy query logic.

The Phase 1 service decisions are:

- owner identity is read from server settings and never accepted from request bodies;
- days are inclusive, one-based, contiguous, and reconciled transactionally;
- item ordering is contiguous and zero-based within each day;
- local `HH:MM` values are interpreted in the trip timezone and DST gaps/folds fail;
- deleting a trip or item is permanent; shrinking over a nonempty day returns `409`;
- manual places are owner-scoped and no provider/map integration is introduced.

## Things explicitly deferred

Do not add now:

- direct Gemini/OpenAI calls,
- memory tables,
- research evidence tables,
- a travel-owned search agent,
- Gmail connector,
- maps provider,
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

Phase 1 is local-first.

Do not deploy or add Neon/GCP secrets as part of feature work unless separately authorized.

Preserve compatibility with the documented deployment path.

## Future handoff expectations

Record the exact commits reviewed, checks actually run, remaining local or
external verification gaps, and whether a later phase needs an ADR. Keep AI,
cloud, maps, reservations, and authentication scope explicitly separated from
the delivered Phase 1 slice.
