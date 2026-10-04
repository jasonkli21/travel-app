# `personal-ai-system` integration

Status: Phase 4 delivered; Phase 5 proposal integration implemented locally and gated off
Date: 2026-10-04

## Rule

`personal-travel-app` is a client of `personal-ai-system`. Keep the boundary
typed and HTTP-based. Do not import `personal_ai.*` Python modules or couple
this repository to Firestore schemas.

## Accepted Phase 4 contract

The travel backend consumes the accepted `research-v1` API:

- `POST /v1/research` creates or replays an idempotent session with a bounded
  question and a `general` or `current` freshness setting;
- `POST /v1/research/{session_id}/run` streams bounded `research.*` progress
  events;
- `GET /v1/research/{session_id}` provides the durable result and citations.

The travel client validates the response schema and session ID, bounds the SSE
stream, accepts only documented event names, reconciles a missing terminal
frame against the durable detail, and maps malformed or interrupted work to a
safe unavailable response. Only completed, unexpired answers with valid HTTP(S)
citations reach the browser. Internal AI queries, attempts, raw evidence, and
provider responses are not forwarded.

The create/run/detail sequence has one elapsed deadline (default 45 seconds,
maximum configurable 50), not just per-I/O timeouts. Streamed JSON is capped at
1 MB, SSE at 128 KiB total, 16 KiB per line/frame and 128 events. SQL projection
runs outside the event loop and releases its transaction before HTTP awaits.
The server returns the earliest citation/session expiry; the UI hides results
after expiry or changes to projected day content. A timeout does not guarantee
the upstream job stopped, so there is no automatic retry with a new key.

## Context and privacy

The current contract accepts a question rather than a structured trip context.
For one owner-scoped selected day, Travel appends a bounded projection of the
trip date range and timezone, selected day date and title, and up to three
itinerary item/place labels and local times. The complete downstream question
is at most 500 characters.

The projection does not append owner, trip, day, item, place, or reservation
IDs, item or reservation notes, reservation details, confirmation codes, or
booking source references. The UI discloses that the question and selected-day
context go to the configured AI service and may be passed to its configured
search provider. The upstream research session and evidence remain owned by
`personal-ai-system`; Travel stores none of them.

The travel-side `PERSONAL_AI_RESEARCH_ENABLED` gate defaults to `false`. The
AI system's `RESEARCH_ENABLED` and its provider/configuration gates are separate
and must be configured independently. Keep manual itinerary, reservation,
place, map, and logistics flows usable when any AI gate or service is off.

## Research and travel state

Research displays evidence; it does not create or edit a place, itinerary item,
or reservation. If a traveler wants to keep a place, they enter its fields in
the manual candidate form. Travel creates the owner-scoped place and
trip-scoped saved-place relationship in one SQL transaction, then the existing
item editor can add that place to a day. No answer text is parsed into domain
fields.

Freshness, citation observation time, and expiry are part of the result
presentation. External observations such as hours, prices, availability, and
travel times are not durable facts.

## Authentication boundary

Local `owner_id="local"` mode is not authentication. The P6.1 identity
foundation adds an optional Google OIDC session boundary for travel-domain
requests; browser-provided owner IDs and service credentials are never trusted.
Research and proposal gates remain off unless separately configured.

When Google mode and an AI gate are enabled, configuration must also select
`google_cloud_run_iam`. The signed-in user's Google ID token is audience-bound
to the configured OAuth client and is verified against the active travel
session owner before request-body parsing. The proxy can source this token only
from its Secure, HttpOnly cookie. Separately, `PersonalAIClient` obtains and
verifies a Cloud Run service ID token for the configured service audience and
service-account email; it sends that credential in `Authorization` and the
user assertion in `X-User-ID-Token`. The upstream independently verifies both
boundaries. The service identity is transport authentication, not the travel
owner. This integration does not claim that live service IAM or upstream user
audience alignment has been provisioned or exercised.

The current local default uses `TRAVEL_AUTH_MODE=local`, and private AI imports
remain disabled. No accepted upstream booking/document extraction contract or
retention policy exists; the research and proposal contracts do not satisfy
that prerequisite. Do not send private booking/document input or create an
extraction endpoint until its separate HTTP/authentication/retention contract
is accepted.

## Accepted itinerary-proposal contract

ADR 0010 pins the accepted local upstream revision and these versions:
`itinerary-proposal-v1`, `travel-itinerary-context-v1`, and
`itinerary-proposal-policy-v2`. The travel client uses the accepted proposal
HTTP routes through `PersonalAIClient`; no upstream Python package or
Firestore schema is imported. The local API supports owner-scoped generate,
detail/by-key lookup, explicit apply, and explicit reject routes.

`PERSONAL_AI_PROPOSALS_ENABLED` defaults to `false`. Independently, upstream
`ITINERARY_PROPOSALS_ENABLED` defaults off, and its provider gate remains
separate. The first UI flow uses `context_only`: it sends a bounded traveler
instruction and projection of trip dates/timezone, day order/titles, item
labels/types/status/schedules, opaque handles, candidate labels, and the
user-selected removal allowlist. It does not attach research sessions. The
backend contract also records `research_evidence` mode and validates cited
evidence when explicitly supplied through the travel API.

Neither mode sends travel IDs, owner IDs, reservation fields, notes, booking
confirmations, source references, or provider payloads. The projection marks
protected itinerary anchors without disclosing their booking details. Travel
persists the instruction only as a one-way fingerprint, plus the bounded
revalidation snapshot, validated operations, deterministic before/after
preview, citation/expiry metadata, and stored apply outcome. Provider response
bodies and raw prompts are not logged or persisted.

Travel caps proposal request context at 48 KiB; the accepted upstream response
is bounded at 128 KiB and the whole external operation has one bounded
deadline. A stable downstream key supports reconciliation after an ambiguous
POST; the client never retries with a new key. Apply requires an expected trip
revision, fresh dependency footprint, and unexpired proposal. SQL applies
operations, one revision, audit state, and the immutable replay result in one
transaction. Repeated apply returns that exact outcome.

The upstream monotonic-deadline conversion was corrected and the local fake
HTTP flow passed under both Uvicorn `auto` (uvloop on this host) and `asyncio`.
Proposal gates still default off and require separate upstream capability,
storage and provider configuration. See the [Phase 5 release
record](releases/phase-5-local-proposals.md).

## Future AI work

Memory-aware research, structured extraction, booking/document import,
and hosted AI use remain deferred. The P6.1 identity foundation is local and
review pending. Later AI output that could
affect travel state must be versioned and typed, checked against current
ownership and hard constraints, previewed to the traveler, and applied only
through existing travel-domain services after explicit confirmation.
