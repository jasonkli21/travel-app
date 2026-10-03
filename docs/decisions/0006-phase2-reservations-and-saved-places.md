# ADR 0006 — Phase 2 reservations and trip saved places

Status: accepted for Phase 2 implementation  
Date: 2026-10-02

## Context

The manual itinerary slice can record ideas and manually entered places, but it
cannot distinguish a booked anchor from an optional candidate. Phase 2 needs a
small relational model for reservations, trip-scoped saved places, links to
itinerary items, and deterministic overlap warnings without introducing booking
provider integrations, AI behavior, maps, or authentication.

Reservations also have local schedule semantics. The trip timezone—not the
browser timezone or a provider-specific default—must remain authoritative.

## Decision

- Add an owner- and trip-scoped `reservations` table with checked string values
  for reservation type and lifecycle status. Store scheduled values as aware
  `starts_at`/`ends_at` instants and expose them through trip-local date/time
  fields.
- Use `tentative`, `confirmed`, and `cancelled` statuses. A cancelled record
  remains manually visible but is excluded from conflict warnings.
- Add nullable `itinerary_items.reservation_id`. An item has at most one
  reservation; a reservation may anchor multiple itinerary items. Enforce
  same-owner and same-trip links in the travel services.
- Add an owner/trip/place `saved_places` join with a unique trip/place pair and
  an optional candidate note. Saving or removing the relationship never
  mutates or deletes the reusable place.
- Keep conflict calculation in deterministic travel-domain code. Compare
  scheduled non-cancelled reservations with non-cancelled scheduled items in
  the same trip, exclude an intentionally linked item, treat a single endpoint
  as a point event, and return explainable conflict context rather than
  mutating state. Both missing endpoints mean unscheduled; boundary-only
  equality is not a conflict.
- When a trip timezone changes, re-resolve each reservation endpoint in the new
  zone while preserving its local date and wall-clock value. Reject invalid DST
  endpoints or an impossible range atomically.
- Treat the four reservation schedule fields as one PATCH group. A client must
  send all four when changing or clearing the schedule; four nulls clear it.
- Extend manual places with optional category, phone, and website metadata.
  Defer provider search, geocoding, maps, and external evidence.

## Consequences

- PostgreSQL remains the authoritative source for both committed bookings and
  manually saved candidates.
- The model supports the useful one-reservation-to-many-item case without a
  new association aggregate, while reserving many-reservations-per-item for a
  later explicit requirement.
- A reservation with no complete schedule can be stored, but it cannot produce
  a time conflict until a schedule is provided.
- Conflict warnings are reproducible and testable, but they are advisory: the
  app does not prevent a user from intentionally keeping overlapping plans.
- Import, authentication, provider terms, and external freshness remain later
  phase decisions rather than implicit properties of these rows.
