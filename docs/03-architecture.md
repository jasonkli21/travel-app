# Architecture

Status: Phase 0–4 delivered; Phase 5 implementation present for review, gates off
Date: 2026-10-04

## System shape

```text
LOCAL
----------------------------------------------------------------------------

Browser
  |
  v
Next.js / React / TypeScript
  |
  v
FastAPI travel API
  | \
  |  \----> PersonalAIClient ----HTTP----> personal-ai-system (local)
  |
  v
PostgreSQL 16

future blobs -> local filesystem


INITIAL CLOUD
----------------------------------------------------------------------------

Cloud Run: travel-web
  |
  v
Cloud Run: travel-api
  | \
  |  \---- HTTPS ----> Cloud Run: personal-ai-system
  |
  v
Neon Postgres

future blobs -> Google Cloud Storage
```

## Repository shape

Use a modular monolith, not independently deployed microservices:

```text
backend/
  src/personal_travel/
    api/routes/     # HTTP adapters
    api/schemas/    # contracts by workflow
    domain/         # shared value types and URL policy
    clients/        # bounded external HTTP clients
    db/
    models/
    repositories/   # SQL statements
    services/       # travel rules and transaction ownership
  migrations/
  tests/

frontend/
  app/
  components/trip-workspace/  # workflow forms
  lib/              # typed API clients/hooks
  tests/            # boundary, geometry and context regressions
  scripts/          # runtime/build helpers

docs/
infrastructure/
```

One API process is enough for initial travel workflows.

## Core boundaries

### Web

Owns rendering, interaction state, route-level composition, and typed calls to the travel API.

Does not:

- access PostgreSQL directly,
- call `personal-ai-system` directly for state mutations,
- enforce authoritative domain invariants only in the browser.

### Travel API

Owns:

- authoritative travel-domain rules,
- validation,
- transactions,
- persistence,
- future authorization,
- AI-client orchestration,
- applying validated proposed actions.

### PostgreSQL

Owns durable authoritative travel records and relational constraints.

The app should use transactions for cross-row invariants.

Trip aggregate reads use shared root locks and mutations use exclusive root
locks, refreshing ORM collections before calculation. SQL rejects duplicate
day/order positions and invalid coordinate pairs/ranges. Contiguity, same-trip
links and ownership remain deterministic service rules. Trip and place
revisions provide optimistic stale-write detection alongside these locks.
Shared reusable places have an independent update lock. Proposal application
locks the trip first, then its shared-place footprint in UUID order, and
revalidates operations and projection integrity in one transaction.

### `personal-ai-system`

External dependency that owns shared AI intelligence:

- memory,
- search/research,
- external evidence,
- model orchestration,
- shared research/entity/ranking behavior.

The travel app must not import internal `personal-ai-system` packages.

### Future blob store

Owns large binary objects/attachments.

The SQL database stores metadata and object references, not large binary payloads.

## Ownership rule

```text
Travel DB = authoritative user/application state
AI memory = durable attributable personal context
AI evidence = time-bounded external observation
AI conclusion = derived interpretation, not app state
```

Do not copy all AI research evidence into the travel DB by default.

Persist a reference/snapshot only when product behavior requires a durable travel record.

## Request flows

### Manual itinerary edit

```text
UI
 -> travel API
 -> validate owner/trip/day/item
 -> transaction
 -> PostgreSQL
 -> response
```

No AI call.

### Phase 4 research

```text
UI
 -> travel API
 -> owner-scoped trip/day validation
 -> bounded question using date range, timezone, selected day, and up to three
    itinerary item/place labels and local times
 -> personal-ai-system `research-v1` create/run/detail API
 -> validate session correlation, event bounds, result state, and citations
 -> UI renders an unexpired cited result
```

The travel-side gate defaults off. No trip, day, owner, item, or reservation
identifiers, notes, or reservation details are sent as context. Research does
not mutate itinerary state. Saving a candidate is a separate user-authored
transaction using the existing place and saved-place tables.

### Gated proposed itinerary edit

The local implementation uses the accepted upstream proposal contract pinned
in ADR 0010. The travel proposal and upstream capability/provider gates are
independent and default off. Travel stores owner-scoped request identity,
contract/policy versions, a bounded immutable base snapshot, typed operations,
preview, expiry, and replay outcome. It does not store raw prompts, private
booking notes, confirmation codes, source references, or provider responses.

```text
UI request
 -> travel API
 -> trip snapshot transaction ends
 -> personal-ai-system accepted typed proposal contract
 -> travel validates response and rechecks the dependency footprint
 -> UI shows full itinerary, operation diff, warnings, revisions and expiry
 -> traveler explicitly applies or rejects
 -> travel revalidates under trip/place locks
 -> one transaction stores operations, outcome and revision
 -> PostgreSQL
```

The client uses a single bounded deadline and response byte limit. Ambiguous
generation outcomes reconcile through the same stable downstream key; they
never trigger an automatic POST with a new key. Apply replay returns the
exact stored outcome. The upstream fake HTTP flow passed with Uvicorn's
asyncio loop. On this host Uvicorn auto-selects uvloop, which exposes an
upstream `loop.time()`/`time.monotonic()` mismatch; see the Phase 5 release
record and keep both generation gates disabled in that runtime until fixed.

## Async work

No queue/background worker in the scaffold.

Async provider orchestration projects immutable values using a worker thread
for synchronous SQL, rolls back the read transaction, and then awaits HTTP.
Whole-operation deadlines and streamed byte limits bound provider work.
Research defaults to a 45-second deadline; logistics provider work is capped
at 30 seconds and 50 eligible transfers. No locks span external waits.

Introduce asynchronous infrastructure only for a concrete requirement such as:

- email/document ingestion,
- expensive batch import,
- long-running research that exceeds request-owned streaming,
- scheduled reservation refresh.

The existence of Pub/Sub in `personal-ai-system` does not imply travel needs its own Pub/Sub topic.

## Portability

The initial cloud target is GCP compute + Neon Postgres.

Preserve a later AWS option by:

- UUID application-generated primary keys,
- UTC `timestamptz`,
- ordinary SQL,
- foreign keys/joins,
- bounded transactions,
- application-level retry boundaries,
- no required Postgres extension in core schema,
- no PL/pgSQL business logic,
- no database triggers unless a later ADR accepts the portability cost.

Aurora DSQL is a possible future target, not an implementation requirement.
It needs a distinct transaction/retry and migration assessment; portable
column types do not imply PostgreSQL locking semantics. See
[ADR 0009](decisions/0009-local-boundaries-and-integrity.md).

## Identity

Phase 0/1 uses `owner_id = "local"` as an identity seam.

This mirrors the personal AI system's useful repository shape while preserving the warning:

> `local` is not authenticated identity.

Before real cloud bookings/email/private documents are stored, add authentication and server-derived owner identity.

Local API/web host and browser-origin allowlists plus loopback bindings protect
against unintended browser access. Clients without Origin remain possible;
this is not an authenticated boundary. Bodies are capped at 64 KiB. Safe error
envelopes, request IDs, route-template/status/duration logs and bounded SQL
waits are delivered now. `/health` is process liveness and `/ready` checks SQL
availability. Hosted metrics, quotas and auth remain later-phase work.
