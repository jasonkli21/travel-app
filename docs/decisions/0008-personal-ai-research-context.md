# ADR 0008: Bounded travel context for external research

- Status: Accepted
- Date: 2026-10-03
- Decision owners: Personal Travel project

## Context

Phase 4 connects the travel application to the accepted `research-v1` contract
in `personal-ai-system`. The contract accepts one bounded question, freshness,
and an idempotency key; it does not accept a separate travel-context object.
Travel data remains authoritative in this application, while research sessions
and evidence belong to the AI service.

## Decision

The travel backend will validate the owner-scoped trip and day, then compose a
deterministic question containing the user's query and a bounded projection of
the trip date range/timezone, selected day date/title, and at most three active
itinerary item/place labels with local times. The final string must fit the
downstream 500-character question limit.

The projection excludes owner, trip, day, item, place, and reservation IDs;
itinerary and reservation notes; reservation details and confirmation codes;
and booking source references. The user is told that their query and selected
day context are sent to the configured AI service and may be passed to its
configured search provider.

Travel consumes the bounded `research-v1` event stream server-to-server,
validates event schema/session correlation, and reads the durable session
detail. Only a completed, unexpired answer and its validated citations are
returned to the browser. Research evidence is not copied into travel tables.
The travel-side gate defaults off and can be enabled independently of the AI
service's research and provider gates.

Research itself does not mutate trips, items, places, or reservations. A
separate manual endpoint creates a user-authored place and its trip-scoped
saved-place row in one SQL transaction. Adding a candidate to an itinerary
continues through the existing explicit item editor.

## Consequences

- The AI boundary stays typed HTTP; no Python package is shared or imported.
- No migration or travel-side research-session table is required.
- No AI output is treated as a durable travel fact or silently applied.
- A disabled, malformed, interrupted, or unavailable AI service produces a
  safe error while manual planning remains available.
- The context projection is deliberately small; itinerary notes, reservation
  context, and richer structured research inputs require a separately reviewed
  contract and product decision.
