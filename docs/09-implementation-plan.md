# Phased implementation plan

Status: proposed roadmap  
Date: 2026-10-02

Each phase should produce a useful, testable vertical slice.

Do not implement later phases simply because their design appears here.

## Phase 0 — Scaffold and architecture

Current phase.

### Deliverables

- repository/docs/ADRs,
- Next.js shell,
- FastAPI shell,
- PostgreSQL Docker Compose,
- SQLAlchemy/Alembic setup,
- initial core schema,
- AI-system HTTP client seam,
- backend/frontend CI checks,
- local setup instructions.

### Definition of done

- backend imports and tests pass,
- migration applies to local PostgreSQL,
- `/health` works,
- frontend builds/type-checks,
- docs accurately distinguish scaffold vs. implementation,
- no external credentials required.

## Phase 1 — Manual trip and itinerary vertical slice

### Build

Backend:

- trip CRUD,
- trip-day generation/validation,
- itinerary item CRUD,
- reorder/move operation,
- optional place creation/attachment,
- owner scoping,
- Pydantic request/response contracts,
- transaction/error behavior,
- repository/service tests.

Frontend:

- trips list,
- create/open trip,
- itinerary day view,
- add/edit/delete item,
- reorder/move,
- status/time/notes editing,
- responsive layout,
- loading/error/empty states.

### Required decisions

- trip-day generation behavior,
- trip date edits,
- itinerary ordering algorithm,
- delete/archive semantics,
- date/time representation.

### Definition of done

A single local user can build and reopen a complete day-by-day itinerary without AI.

## Phase 2 — Reservations and saved places

Build reservation schema, saved-place relationship, booking/tentative distinction, richer place metadata, trip overview/reservation view, links between itinerary items and reservations, and deterministic conflict indicators.

**Outcome:** the application can represent booked anchors and optional candidates separately.

## Phase 3 — Maps and travel logistics

Decide map/place provider, attribution/retention terms, travel-time provider, and free-tier implications.

Then add map view, itinerary markers, place search/import, basic route/travel-time enrichment, and logistics warnings.

Do not require PostGIS unless measured query needs justify it.

## Phase 4 — First `personal-ai-system` integration

Prerequisite: accepted AI-system contract for bounded research.

Build:

- typed research client,
- active-trip/day context projection,
- research panel,
- evidence/citation rendering,
- candidate save/add workflow,
- graceful AI-unavailable behavior.

Research results do not write itinerary state automatically.

## Phase 5 — Structured AI proposals

Build:

- versioned itinerary-patch contract,
- current-state/version checks,
- proposal preview/diff,
- deterministic revalidation,
- atomic apply,
- audit metadata.

Example operations: add candidate item, move item, update time, remove optional item.

AI suggestions remain proposals.

## Phase 6 — Booking/document import

Prerequisites:

- authentication,
- secure blob handling if uploads exist,
- explicit connector/provider policy.

Build incrementally:

- manual email/document import first,
- extraction through `personal-ai-system`,
- typed candidate reservation,
- source reference,
- review/confirm UI,
- idempotency/deduplication.

Gmail automation should come after manual import proves the schema/workflow.

## Phase 7 — Rich travel research

Expand only after the shared AI research platform supports it.

Potential areas:

- neighborhoods,
- hotels,
- food,
- activities,
- transit,
- day trips,
- flights.

Combine:

```text
hard trip constraints
+ fresh evidence
+ travel-domain features
+ relevant AI memory preferences
-> explainable candidates
```

Hard constraints remain deterministic.

## Phase 8 — Attachments, exports, travel mode

Potential work:

- GCS-backed documents,
- offline-friendly itinerary export,
- calendar export,
- printable/shareable trip summary,
- reservation document access.

A full offline-sync/mobile architecture is still not implied.

## Phase 9 — Operational hardening

- auth/authorization review,
- backup/export/delete tools,
- logging/observability,
- provider rate/cost safeguards,
- cloud smoke tests,
- data migration/versioning,
- security review,
- deployment runbook.

## Later ideas

Only after core UX is strong:

- collaborative trips,
- shared itineraries,
- automatic reservation monitoring,
- travel change notifications,
- calendar/email continuous sync,
- richer preference learning,
- mobile-native client.

These should not influence Phase 1 architecture unless a concrete invariant requires it.
