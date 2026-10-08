# Architecture

Status: Architecture reference; current delivery status is maintained in [current-state.md](current-state.md).
Date: 2026-10-08

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

private booking sources and trip-document blobs -> LocalSourceStore on local filesystem


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

Google Cloud Storage -> not implemented; hosted private-file features fail closed
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

- canonical trips, itinerary state and ordering, bookings/reservations, and
  travel-domain validation;
- request authorization and owner-scoped access;
- travel persistence and transaction boundaries;
- final mutation/application of reviewed travel changes;
- AI-client orchestration.

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

External dependency that owns reusable AI-platform capabilities:

- model/provider access and reusable orchestration;
- evidence, context, memory, and retrieval machinery;
- shared research, decision, and comparison capabilities.

It returns evidence, recommendations, or typed proposals. It does not own
canonical Travel records, Travel authorization, or Travel persistence; Travel
rechecks ownership and current state, applies travel rules, previews changes,
and performs final canonical writes. The travel app must not import internal
`personal-ai-system` packages.

Personal AI has a registered Travel application definition and registry
metadata. The direct capability endpoints currently called by Travel still
require standalone Personal AI application scope. Travel omits
`X-Application-ID: travel` from these calls. Registry metadata does not itself
make the direct research, extraction, proposal, or comparison endpoints
application-scoped. Migrate only after Personal AI exposes those capabilities
through its supported application-integration contract; no workspace identity
or `workspace_id = trip_id` mapping is defined.

### Private source and attachment storage

Travel stores booking-source and trip/reservation attachment bytes in
`LocalSourceStore`, on a separately configured local filesystem path. SQL owns
their metadata, lifecycle, authorization links, hashes, and opaque object
references; it does not store large binary payloads.

The local filesystem is the only implemented private-object store. GCS or any
other durable shared cloud store is not implemented. Hosted configuration
rejects enabling private imports or attachments until a supported shared
object store is available and accepted.

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

The current `research-v1` direct endpoint call uses standalone Personal AI
application scope and sends no `X-Application-ID: travel` header. The registered
Travel application definition is not the scope for this direct interface.

### Phase 6 booking-document extraction and import

```text
verified owner uploads a bounded text/PDF source
 -> Travel stores original bytes and metadata in LocalSourceStore and parses
    bounded UTF-8 text
 -> explicit submit sends the text, source-text digest, and stable idempotency
    key to booking-document-extraction-v1
 -> Personal AI returns bounded candidates with literal source spans and
    uncertainty fields
 -> Travel validates source/import/trip ownership, digest, and candidate data
 -> traveler reviews fields and explicitly confirms one reservation batch
 -> Travel writes canonical reservations atomically; outcomes are replayable
```

The upstream receives only the text explicitly submitted for extraction; it
does not receive Travel identifiers, access Travel persistence, or write
reservations. Its output remains advisory. Travel owns the review, uncertainty
resolution, authorization, travel-time interpretation, validation, and final
reservation writes. Intake, extraction, upstream provider use, and retention
are separately gated. Local private storage requires verified Google identity;
hosted private-file features are rejected while only local storage exists.

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

The direct comparison endpoints use standalone Personal AI application scope.
Travel does not retrieve shared memory preferences as part of this flow.

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

The direct proposal endpoints also use standalone Personal AI application
scope. A returned proposal cannot mutate Travel state until Travel validates
and previews it and the traveler explicitly applies it.

## Async work

No queue/background worker in the scaffold.

Async provider orchestration projects immutable values using a worker thread
for synchronous SQL, rolls back the read transaction, and then awaits HTTP.
Whole-operation deadlines and streamed byte limits bound provider work.
Research defaults to a 45-second deadline; logistics provider work is capped
at 30 seconds and 50 eligible transfers. No locks span external waits.

Introduce asynchronous infrastructure only for a concrete requirement such as:

- mailbox ingestion or a broader ingestion flow beyond the existing bounded
  booking-source import,
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
enablement gates. Booking-source intake and trip/reservation attachments are
implemented behind separate default-off switches and require verified Google
identity; local-auth mode cannot upload or read private files. Source and
attachment bytes use `LocalSourceStore` outside the application tree. With
explicit submission, bounded extracted text is sent to the separately gated
`booking-document-extraction-v1` HTTP capability. Validated candidates remain
suggestions until the owner resolves uncertainties and commits one atomic
reservation batch. Source bytes, excerpts, and extraction results follow
explicit deletion/expiry rules. Hosted configuration rejects either private
feature while local filesystem storage is the only implementation. See
[ADR 0011](decisions/0011-phase6-google-identity-and-ai-auth.md),
[ADR 0012](decisions/0012-phase6-private-source-storage.md),
[ADR 0013](decisions/0013-phase6-booking-document-import.md), and
[ADR 0015](decisions/0015-phase8-attachments-and-exports.md).

Local API/web host and browser-origin allowlists plus loopback bindings protect
against unintended browser access in local mode. Local clients without Origin
remain possible by design; local mode is not an authenticated boundary.
Ordinary JSON bodies are capped at 64 KiB. The exact authenticated P6.2 upload
route has separate streamed 1 MiB text and 10 MiB PDF limits and a 30-second
receive deadline. Safe error envelopes, request IDs, route-template/status/
duration logs and bounded SQL waits remain in place. `/health` and `/ready`
remain public probes; non-mutating OpenAPI documentation also remains public.
Provider quotas are implemented; hosted metrics and multi-instance quota
behavior remain unverified production gates.
