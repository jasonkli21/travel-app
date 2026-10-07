# Architecture

Status: Architecture reference; current delivery status is maintained in [current-state.md](current-state.md).
Date: 2026-10-07

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

private source and trip-document blobs -> opaque local filesystem store


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

Google Cloud Storage -> separately authorized and not implemented
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

### Phase 7 domain comparison

```text
UI submits category, search text, reference place, radius, result limit and key
 -> Travel snapshots trip/place revisions, then ends the SQL transaction
 -> PersonalAIClient calls the accepted typed travel-domain lookup
 -> Travel validates owner, constraints, claim/citation correlation and freshness
 -> Travel checks typed place category and geographic radius deterministically
 -> UI renders bounded results only for the current revision/expiry footprint
 -> traveler separately saves a reviewed Nominatim place or starts Phase 5 proposal
```

The travel gate and web-build gate default off. The request sends the search
text and reference coordinates, with no trip/place IDs or itinerary content.
Travel stores no comparison record. Only type, location, distance, ranking,
source and expiry are supported; dates, prices, hours, accessibility,
availability, travel duration, and preference retrieval are unavailable. A
saved place reuses the existing owner-scoped `places` and `saved_places`
records and keeps verified OpenStreetMap attribution.

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
never trigger an automatic POST with a new key or reset the external request
budget. Apply replay returns the exact stored outcome. The upstream monotonic
deadline is converted to a remaining duration at async timeout boundaries; the
local fake HTTP flow passes with Uvicorn's `auto` (uvloop here) and `asyncio`
loops. Proposal gates remain off by default after independent local review.

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

Local mode uses `owner_id = "local"` as an explicit development seam.

> `local` is not authenticated identity.

The local seam is not authenticated identity. The implemented P6.1 foundation
adds optional Google authorization-code OIDC, S256 PKCE, one normalized
verified-email allowlist, and a server-owned stable owner derived from the
canonical Google issuer and `sub`. The backend verifies Google ID-token
signature, issuer, audience, expiry, issue time, verified email, and (during
login) nonce through `google-auth` and bounded Google key retrieval. Browser
sessions use random opaque IDs stored only as SHA-256 digests, expire without
sliding, and can be revoked by logout. A Next.js server callback/proxy forwards
only allowlisted cookies and the backend independently checks every identity.

All domain APIs derive the owner from the request principal before body parsing
or domain/provider SQL. Browser owner headers and arbitrary bearer credentials
are rejected. Unsafe requests require a matching CSRF proof and an allowed
Origin; private HTML pages are dynamic and check the active backend session.
Google mode fails closed on bad sessions or unavailable verification storage.
An explicit, backed-up CLI moves the full `local` owner graph only after a
verified target identity exists; it never runs during first sign-in.

`TRAVEL_AUTH_MODE=local` remains the default and keeps local CRUD
unauthenticated behind configured local host/origin checks. Live Google OAuth,
upstream user-audience alignment, and Cloud Run service IAM remain separate
enablement gates. The locally reviewed implementation adds a Google-session-only, off-by-default
source lifecycle with opaque files outside the application tree; local mode
cannot upload or read these sources. With explicit submission, bounded extracted
text is sent to the separately gated `booking-document-extraction-v1` HTTP
capability. Validated candidates remain suggestions until the owner corrects
uncertainties and commits one atomic reservation batch. Source bytes, excerpts,
and result retention follow explicit deletion/expiry rules. General attachments
remain unimplemented. See [ADR 0011](decisions/0011-phase6-google-identity-and-ai-auth.md),
[ADR 0012](decisions/0012-phase6-private-source-storage.md), and
[ADR 0013](decisions/0013-phase6-booking-document-import.md).

Local API/web host and browser-origin allowlists plus loopback bindings protect
against unintended browser access in local mode. Local clients without Origin
remain possible by design; local mode is not an authenticated boundary.
Ordinary JSON bodies are capped at 64 KiB. The exact authenticated P6.2 upload
route has separate streamed 1 MiB text and 10 MiB PDF limits and a 30-second
receive deadline. Safe error envelopes, request IDs, route-template/status/
duration logs and bounded SQL waits remain in place. `/health` and `/ready`
remain public probes; non-mutating OpenAPI documentation also remains public.
Hosted metrics and quotas remain later-phase work.
