# `personal-ai-system` integration

Status: Phase 4 `research-v1` consumer implemented locally; travel gate off by default  
Date: 2026-10-03

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

The local `owner_id="local"` seam is not authentication. This integration is
for local-first use only. Private cloud data, booking imports, and user-specific
AI sessions require an authenticated identity and an explicitly authorized
service boundary before implementation.

## Future AI work

Memory-aware research, structured extraction, itinerary proposals, and
model-driven actions remain deferred. Later AI output that could affect travel
state must be versioned and typed, checked against current ownership and hard
constraints, previewed to the traveler, and applied only through existing
travel-domain services after explicit confirmation.
