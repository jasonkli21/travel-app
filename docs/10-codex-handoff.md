# Codex handoff

Status: Phases 1–5 delivered and independently reviewed locally; P6.0/P6.1
identity and P6.2 secure-source lifecycle implemented locally; P6.2 review
findings repaired; whole-Phase 6 review remains pending; P6.3–P6.5 remain planned
Date: 2026-10-04

The identity and P6.2 checkpoints record their local implementation evidence and
external gates. Continue the remaining Phase 6 implementation in one combined
work stage, then review Phase 6 as a whole and stop before Phase 7 as requested.

## Objective

The Phase 1 manual itinerary planner, Phase 2 reservations/saved-places slice,
Phase 3 maps/location/logistics slice, and Phase 4 bounded AI research consumer
are implemented locally. Review evidence and commit references are recorded in
the phase release documents.

Phase 5 P5.0–P5.5 is implemented and independently reviewed locally. The
accepted upstream contract, durable storage, lifecycle, apply/replay, rejection,
gated generation client, and explicit review UI are described in
[`releases/phase-5-local-proposals.md`](releases/phase-5-local-proposals.md).
All proposal gates remain off by default. The earlier groundwork record stays
in [`releases/phase-5-groundwork.md`](releases/phase-5-groundwork.md).

The Phase 4 consumer uses the accepted `research-v1` API in
`personal-ai-system`; do not change that API from this repository. P6.0/P6.1
adds an optional verified Google identity/session boundary and explicit
local-owner migration. P6.2 adds a default-off, authenticated local source
lifecycle. Live OAuth/service IAM remains unconfigured. Extraction,
confirmation, import UI, and cloud deployment remain deferred.
Phase 5 proposal generation is implemented and reviewed locally, with gates
default off. P6.2 remediation introduces no extraction, confirmation, or import UI.

The [comprehensive Phase 0–4 audit](reviews/phase-0-4-audit.md) documents current
fixes and verification. Migration `0011` is the current head; `0005` still
requires online legacy-data inspection/repair and a pre-upgrade backup before
upgrading an existing database. See local development and ADR 0009 before
changing concurrency or local HTTP boundaries.

## Next implementation work

Independent Phase 5 review is closed. The Phase 6 identity and P6.2
source-lifecycle implementation are local; P6.2 review findings are repaired,
with whole-Phase 6 review pending. Accepted extraction, booking confirmation
and review UI remain later work. Their scope and release evidence
are recorded in the [identity checkpoint](releases/phase-6-identity.md), the
[P6.2 checkpoint](releases/phase-6-private-sources.md), and the coordinator
file. The upstream uvloop clock-domain issue was fixed in
`personal-ai-system` revision `6045f004fbdc4887c2bb67da9ae19a571314fc27` and
verified over local HTTP with Uvicorn `auto` and `asyncio`; default-off gates
remain intentional after local review.

Detailed [Phase 5](phase-5-implementation-plan.md),
[Phase 6](phase-6-implementation-plan.md),
[Phase 7](phase-7-implementation-plan.md),
[Phase 8](phase-8-implementation-plan.md) and
[Phase 9](phase-9-implementation-plan.md) plans are now recorded against
`56c0cbf`. They do not mark any later capability delivered. Phase 5's accepted
upstream proposal contract and P5.3 durable lifecycle/apply are implemented;
the independent review and remediation are recorded in
[`reviews/phase-5-lifecycle-review.md`](reviews/phase-5-lifecycle-review.md).
Existing writes now accept an optional `X-Expected-Revision`, which the web
proxy explicitly allowlists and forwards; omitted headers preserve legacy
compatibility. A successful stale/uncertain recovery reload resets open editor
drafts against the newly loaded snapshot. Keep proposal generation gated; do
not invent upstream APIs in this repository.

Phase 6's P6.0/P6.1 identity stage establishes verified identity before private
imports, with explicit local-owner migration. P6.2 implements the off-by-default
local source/blob lifecycle, without extraction, confirmation, or UI. Continue
with P6.3–P6.5, record the final release evidence, then stop before Phase 7.
Phase 7 requires category/evidence/rights contracts and consented preference
projection. Phase 8 adds private document access and explicit static snapshots,
not offline synchronization. Phase 9 proves auth, quotas, SQL/blob restore and
release/deployment controls; it does not defer basic security until then.
Cloud actions continue to require separate authorization.

## Read first

1. root `AGENTS.md`
2. `docs/01-product-brief.md`
3. `docs/02-product-design.md`
4. `docs/03-architecture.md`
5. `docs/04-data-model.md`
6. `docs/09-implementation-plan.md`
7. ADRs under `docs/decisions/`

Use `docs/06-ai-integration.md`, the Phase 4 plan, and ADR 0008 to preserve the
consumer-side context and evidence boundary.

## Current implementation contents

Backend:

- FastAPI app and `/health`.
- Database readiness at `/ready`, safe error/request-ID/logging behavior and
  explicit local host/origin/request-size boundaries.
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
- Migration `0005` repairs legacy moved-item dates/order and enforces unique
  day/order plus paired/ranged coordinates. Timed moves preserve local times
  on the destination date and reject DST gaps/folds transactionally.
- Migration `0006` adds nonnegative trip and reusable-place revisions. Existing
  write routes accept optional expected revisions; stale supplied revisions
  fail before mutation with a stable 409, while unchanged values do not bump
  revisions. Shared-place edits do not fan out to referencing trips.
- Internal strict bounded proposal DTOs and immutable full-itinerary preview
  validation reuse manual DST and reservation-overlap rules. These models are
  not routed, stored, sent upstream, or applied.
- typed Pydantic request/response contracts and a common error envelope.
- deterministic inclusive day generation and date-range reconciliation.
- local-time item conversion with IANA timezone/DST validation.
- transactional contiguous ordering and cross-day move behavior.
- trip-root row locking for concurrent item-order mutations.
- Shared trip-root aggregate reads, refreshed ORM collections, independent
  place-update locks and database-side summary counts.
- typed `PersonalAIClient` health and gated `research-v1` operations, including
  bounded SSE consumption and durable detail reconciliation.
- optional Google OIDC verification, stable `issuer + NUL + sub` owner mapping,
  bounded Google-key cache/fetch, and a single verified-email allowlist.
- opaque database-backed browser sessions, CSRF digests, expiry/revocation,
  state/nonce/S256 PKCE login attempts, and safe logout.
- request-principal owner authorization before domain body/provider access,
  including research/proposal routes; local mode remains explicit and
  unauthenticated.
- typed separation of the AI user ID-token assertion from the independent
  Cloud Run service IAM transport credential.
- migration `0009` identity/session/login-attempt/migration-audit tables and
  explicit backed-up local-owner graph migration tooling.
- migrations `0010`–`0011` owner-scoped booking-import/private-source metadata
  and durable import replay records after source deletion.
- owner-scoped trip/day research with bounded day context and safe errors.
- Streamed JSON/SSE byte bounds and whole-operation deadlines; synchronous
  SQL projections run in worker threads and release locks before provider work.
- atomic manual place-plus-trip-candidate creation through existing tables.
- pytest/ruff/mypy configuration.

Frontend:

- Next.js App Router trips list and trip workspace.
- same-origin `/api/v1/*` proxy and centralized typed client.
- protected dynamic private pages, a minimal Google sign-in/callback/session
  flow, logout/expiry UI, and an allowlisted same-origin auth proxy.
- server-only forwarding of opaque session/CSRF cookies; the AI user token is
  sourced only from its HttpOnly cookie and never accepted from browser headers.
- responsive trip/day/item/place/reservation/candidate forms with loading,
  empty, and error states.
- Workflow form modules, paired manual coordinate entry, synchronous mutation
  guards, revision headers forwarded through the explicit proxy allowlist, and
  a blocked/reloadable stale state on precondition or refresh failure. A
  successful explicit recovery reload closes/resets open editors; ordinary
  refreshes preserve drafts.
- accessible move-up/move-down and destination-day controls.
- trip overview counts, reservation status distinction, linked-item context,
  and visible conflict warnings.
- selected-day research form, transfer disclosure, cited unexpired result
  rendering, and a user-entered manual candidate form.
- TypeScript/ESLint setup and production build verification.
- Boundary/geometry/context tests under `frontend/tests/`, also executed in CI.

Infrastructure:

- local PostgreSQL 16 through Docker Compose.
- Dockerfiles for API/web.
- GCP/Neon deployment documented but not implemented.

## Phase 1–3 verification

Current review evidence: 86 backend tests passed with PostgreSQL 16.15 and zero
skips, 14 frontend Node tests passed, Ruff/mypy/ESLint/TypeScript/build/package
checks passed, plus an isolated standalone browser smoke check. Live providers,
Docker execution, hosted CI and cloud deployment were not verified. Historical
release records below retain the checks actually performed at their release.

The October 2 Phase 0 review and corrections are recorded in
[`releases/phase-0-scaffold.md`](releases/phase-0-scaffold.md). Phase 1 evidence,
commit references, and environment-specific limitations are recorded in
[`releases/phase-1-itinerary.md`](releases/phase-1-itinerary.md).
Phase 2 evidence, commit references, and environment-specific limitations are
recorded in [`releases/phase-2-reservations.md`](releases/phase-2-reservations.md).
Phase 3 evidence, commit references, and provider/database limitations are in
[`releases/phase-3-maps-logistics.md`](releases/phase-3-maps-logistics.md).
Phase 4 evidence, review findings, commit references, and external service
limitations are in [`releases/phase-4-ai-research.md`](releases/phase-4-ai-research.md).
Phase 5 groundwork evidence, exact local checks, and external gates are in
[`releases/phase-5-groundwork.md`](releases/phase-5-groundwork.md).

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
POST   /v1/trips/{trip_id}/saved-places/manual
POST   /v1/trips/{trip_id}/research
```

The route -> service -> repository boundary is implemented and remains
intentionally lightweight; routes do not contain SQLAlchemy query logic.

The Phase 1 service decisions are:

- owner identity is read from server settings and never accepted from request bodies;
- days are inclusive, one-based, contiguous, and reconciled transactionally;
- item ordering is contiguous and zero-based within each day;
- local `HH:MM` values are interpreted in the trip timezone and DST gaps/folds fail;
- cross-day moves re-resolve those times on the destination date;
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

The Phase 4 service decisions are:

- the travel-side research gate defaults off and the AI system's research and
  provider gates remain separately controlled;
- only selected-day date/timezone/title and up to three item/place labels and
  local times are projected, with a 190-character context and 500-character
  final-question budget;
- owner/trip/day/item/place/reservation IDs, itinerary/reservation notes,
  confirmation data, and booking references are excluded from the context;
- only completed, unexpired answers with bounded validated citations are sent
  to the browser; a missing SSE terminal event is reconciled against durable
  session detail;
- research does not mutate travel state; manual candidate creation is one SQL
  transaction, and itinerary addition remains in the existing item editor;
- no research session, evidence, migration, or AI-derived place is persisted
  by the travel application.

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

Use only the accepted `research-v1` routes through the typed HTTP client. Do
not import the AI repository's Python packages or forward its internal session
records to the browser. New AI capabilities need an accepted upstream contract
and a separate plan/review.

## Cloud rule

Phases 1–3 are local-first.

Do not deploy or add Neon/GCP secrets as part of feature work unless separately authorized.

Preserve compatibility with the documented deployment path.

## Future handoff expectations

Record the exact commits reviewed, checks actually run, remaining local or
external verification gaps, and whether a later phase needs an ADR. Keep AI,
cloud, external imports, and authentication scope explicitly separated from
the delivered Phase 3 slice.

## Phase 6 identity handoff

P6.0 identity decision, P6.1 verified-owner foundation, and P6.2 secure-source
lifecycle are implemented locally, pending review. Read ADRs 0011–0012 and the
[identity](releases/phase-6-identity.md) and
[P6.2 source](releases/phase-6-private-sources.md) checkpoints for contracts,
checks, and remaining gates. The private-import flag remains off by default and
local mode is denied. No extraction, reservation confirmation, or review UI is
implemented. Do not treat synthetic local verification as live Google OAuth,
Cloud Run IAM, or an accepted extraction/retention contract.
