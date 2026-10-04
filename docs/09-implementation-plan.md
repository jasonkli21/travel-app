# Phased implementation plan

Status: active roadmap; Phases 1–5 delivered and reviewed locally; Phase 6 next
Date: 2026-10-03

Each phase should produce a useful, testable vertical slice.

Do not implement later phases simply because their design appears here.

The [Phase 0–4 audit](reviews/phase-0-4-audit.md) corrects the original plans
and documents actual verification. Local delivery is not public production
readiness: authentication remains a prerequisite for private imports and cloud
data. Basic local security, readiness, deadlines and migration recovery cannot
be postponed to Phase 9.

## Phase 0 — Scaffold and architecture

Delivered.

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
- database-backed suites execute in CI against real migrations with no skips,
- local host/origin/body boundaries, safe errors, request IDs, bounded database
  waits and separate liveness/readiness work without provider credentials,
- online migration and backup/restore guidance covers existing data.

## Phase 1 — Manual trip and itinerary vertical slice (delivered)

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

A single local user can build and reopen a complete day-by-day itinerary without
AI. The delivered implementation and verification evidence are recorded in
[`releases/phase-1-itinerary.md`](releases/phase-1-itinerary.md).
Timed moves preserve local times on the destination date and reject DST gaps/
folds atomically. SQL enforces day/order uniqueness and coordinate integrity;
shared aggregate reads and locked writes preserve consistent relational state.
Successful writes remain successful if a following refresh fails; stale
workspaces block editing until recovery.

## Phase 2 — Reservations and saved places (delivered)

The task-level plan is recorded in
[`phase-2-implementation-plan.md`](phase-2-implementation-plan.md).

Build reservation schema, saved-place relationship, booking/tentative distinction, richer place metadata, trip overview/reservation view, links between itinerary items and reservations, and deterministic conflict indicators.

The delivered implementation and verification evidence are recorded in
[`releases/phase-2-reservations.md`](releases/phase-2-reservations.md).

**Outcome:** the application can represent booked anchors and optional candidates separately.

## Phase 3 — Maps and travel logistics

The task-level plan and provider decision are recorded in
[`phase-3-implementation-plan.md`](phase-3-implementation-plan.md) and
[`decisions/0007-geoapify-maps-and-logistics.md`](decisions/0007-geoapify-maps-and-logistics.md).

The trip map, explicit place search/import, and on-demand itinerary logistics
slice are delivered locally. Release evidence and review fixes are recorded in
[`releases/phase-3-maps-logistics.md`](releases/phase-3-maps-logistics.md).
Do not require PostGIS unless measured query needs justify it.

## Phase 4 — First `personal-ai-system` integration

Delivered locally against the accepted `research-v1` contract in the
`personal-ai-system` repository. The travel feature remains disabled by
default; research and provider gates must be configured independently.

Delivered functionality:

- typed research client,
- active-trip/day context projection,
- research panel,
- evidence/citation rendering,
- candidate save/add workflow,
- graceful AI-unavailable behavior.

Research results do not write itinerary state automatically.

The Phase 4 plan, consumer boundary decision, and release evidence are recorded
in [`phase-4-implementation-plan.md`](phase-4-implementation-plan.md),
[`decisions/0008-personal-ai-research-context.md`](decisions/0008-personal-ai-research-context.md),
and [`releases/phase-4-ai-research.md`](releases/phase-4-ai-research.md).

## Phase 5 — Structured AI proposals

Detailed plan: [Phase 5](phase-5-implementation-plan.md). The P5.0–P5.5 local
implementation has passed independent local review; proposal gates remain off by
default. It pins the accepted upstream contract, adds durable owner-scoped
storage and atomic apply/replay, gated generation, and the explicit review UI.
The first removal operation requires an explicit user-selected removable-item
allowlist; status alone does not establish optionality. The upstream Uvicorn
clock-domain deadline issue is fixed and fake HTTP verification passes with
both `auto` and `asyncio`; independent Phase 5 review remains pending.

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

Detailed plan: [Phase 6](phase-6-implementation-plan.md). Planned, not delivered.
Authentication is the first work package before any private input. This phase
introduces only the minimum secure local source/blob lifecycle needed for
manual pasted text/PDF import; Phase 8 extends the same lifecycle.

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

Gmail automation remains outside the first Phase 6 slice and requires a
separate connector/consent plan after manual import proves the workflow.

## Phase 7 — Rich travel research

Detailed plan: [Phase 7](phase-7-implementation-plan.md). Planned, not delivered.
Start with accepted food/activity/neighborhood/day-trip categories. Hotel,
flight and transit comparison require separately verified category contracts.
Typed money/date/availability constraints are deterministic; optional memory
retrieval requires verified ownership, consent and a narrow projection.

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

Detailed plan: [Phase 8](phase-8-implementation-plan.md). Planned, not delivered.
Reuse Phase 6 authentication/blob lifecycle, add trip/reservation attachment
access and explicit revision-stamped ICS/printable/static/JSON snapshots.
GCS requires separate authorization; local storage remains first-class.

Potential work:

- GCS-backed documents,
- offline-friendly itinerary export,
- calendar export,
- printable/shareable trip summary,
- reservation document access.

A full offline-sync/mobile architecture is still not implied.

## Phase 9 — Operational hardening

Detailed plan: [Phase 9](phase-9-implementation-plan.md). Planned, not delivered.
This reviews established authentication and basic local operability; it does
not postpone those prerequisites. Prove resource/cost controls, SQL/blob
restore, locked releases/migration jobs and separately authorized hosted smoke
before claiming production readiness.

- auth/authorization review,
- backup/export/delete tools,
- logging/observability,
- provider rate/cost safeguards,
- cloud smoke tests,
- data migration/versioning,
- security review,
- deployment runbook.

## Remaining-phase sequencing

```text
Reviewed 0–4
  -> 5: revisions + accepted proposal contract + preview/apply
  -> 6: verified identity first -> secure sources -> reviewed imports
  -> 7: accepted rich categories + constraints/consented preferences
  -> 8: reuse identity/blob lifecycle -> attachments and static exports
  -> 9: recovery/security/cost/release proof -> authorized hosted smoke
```

Phase 8 exports do not depend on completing every Phase 7 research category.
External contract/credential gates must be reported explicitly; local
groundwork or fake-backed tests alone do not complete an enabled integration.
The five detailed plans use the reviewed `56c0cbf` baseline and specify goals,
boundaries, contracts, work packages, failure matrices, commits and exit gates.
These documents describe planned scope; release notes identify locally
implemented, reviewed, gated, and externally unverified work separately.

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
