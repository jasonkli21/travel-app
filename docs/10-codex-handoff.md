# Codex handoff

Status: initial scaffold handoff  
Date: 2026-10-02

## Objective

Continue `personal-travel-app` from the current Phase 0 scaffold into a small, testable Phase 1 manual itinerary planner.

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

## Current scaffold contents

Backend:

- FastAPI app and `/health`.
- Pydantic settings.
- SQLAlchemy engine/session.
- initial `Trip`, `TripDay`, `Place`, `ItineraryItem` models.
- Alembic initial migration.
- repository protocol + SQLAlchemy implementation example for trips.
- `PersonalAIClient` with health only.
- pytest/ruff/mypy configuration.

Frontend:

- Next.js App Router shell.
- responsive three-pane itinerary concept.
- backend health proxy.
- TypeScript/ESLint setup.

Infrastructure:

- local PostgreSQL 16 through Docker Compose.
- Dockerfiles for API/web.
- GCP/Neon deployment documented but not implemented.

## Scaffold verification before feature work

The October 2 Phase 0 review and corrections are recorded in
[`releases/phase-0-scaffold.md`](releases/phase-0-scaffold.md). Locked installs,
backend/frontend checks, production startup, and a real local PostgreSQL 16
migration round trip have been verified. Docker images/Compose and hosted CI
remain unverified in that environment. The application is still Phase 0.

Before modifying code, verify:

1. backend dependency resolution,
2. migration correctness against PostgreSQL 16,
3. ORM/migration parity,
4. frontend dependency compatibility/current Next.js conventions,
5. Dockerfile correctness,
6. CI configuration,
7. environment-file loading from repository root/backend working directories.

The identified lint/typecheck, environment-loading, Alembic URL escaping, and
AI health error-boundary defects have been corrected. Preserve the committed
dependency locks and use frozen installs. Recheck remaining environment-specific
gaps when Docker is available; do not treat documented cloud targets as deployed.

Record what was actually run.

## Then propose a Phase 1 task plan

Do not immediately implement the entire phase.

Create/review a task-level Phase 1 plan covering API contracts, data invariants, repository/service boundaries, and the frontend slice.

Candidate endpoints:

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
```

Exact routes may change after review.

### Data invariants to resolve

- how trip days are created/updated after date edits,
- whether a trip must have one row per calendar day,
- item timestamps must align with the intended trip day,
- ordering/move transaction semantics,
- ownership validation,
- deletion behavior.

Database constraints already cover trip and item start/end ordering; service-level errors still need defined envelopes.

### Repository/service boundary

Routes should not contain SQLAlchemy query logic.

Prefer:

```text
route -> service -> repository/unit-of-work -> DB
```

Keep this lightweight; do not build a framework around it.

## Tests expected in Phase 1

Backend:

- create/list/get trip,
- invalid trip date range,
- day-generation/date-edit edge cases,
- item CRUD,
- move/reorder,
- cross-trip/cross-owner rejection,
- transaction rollback,
- not-found/error envelopes.

Frontend:

- type checks/lint,
- key component interaction tests if a test framework is added,
- at minimum one real API/UI local smoke path documented.

Do not add Playwright until an end-to-end flow exists to justify it.

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

## Expected Codex output for the initial handoff

Before broad implementation, provide:

1. scaffold health assessment,
2. defects/risks found,
3. proposed minimal corrections,
4. Phase 1 task decomposition,
5. any ADRs required,
6. exact verification commands,
7. confirmation that AI/cloud scope remains deferred.
