# ADR 0010 — Phase 5 proposal safety and revision policy

**Status:** accepted for local itinerary proposal integration
**Date:** 2026-10-03
**Policy alignment:** 2026-10-04
**Context:** [Phase 0–4 audit](../reviews/phase-0-4-audit.md) and [Phase 5 plan](../phase-5-implementation-plan.md)

## Context

The upstream itinerary proposal capability was independently reviewed and
accepted at `personal-ai-system` revision
`8535cad3a146b1a19cab0958c439f170d19b8095`. The pinned versions are
`itinerary-proposal-v1`, context `travel-itinerary-context-v1`, and policy
`itinerary-proposal-policy-v2`. The consumer calls only the documented
`POST /v1/travel/itinerary-proposals`, `GET
/v1/travel/itinerary-proposals/{proposal_id}`, and `GET
/v1/travel/itinerary-proposals/by-key/{idempotency_key}` routes. The upstream
feature and provider gates remain independently controlled and default off.

The upstream result explicitly distinguishes `context_only` from
`research_evidence`. Context-only proposals may use only the traveler
instruction and typed itinerary projection and cannot make current external
claims. Research-evidence operations each need verified citations. Failed,
expired, or invalid supplied evidence is never relabeled as context-only.
Travel accepts neither insufficient/uncited states nor responses with
unsupported policy/schema versions, mismatched opaque handles, invalid
operation-support correlations, or invalid citations. Travel validates every
accepted operation and retains the immutable SQL footprint and preview.

## Permitted operations

The only proposal operation kinds are:

1. Add an itinerary item from a place already saved as a candidate for this
   trip. The server derives its title from that place; the proposal cannot
   create or edit place metadata.
2. Move an existing item to another trip day at a validated insertion position.
3. Set or clear an existing item's local start/end times.
4. Remove an existing item only when the traveler explicitly selected it as
   removable for this proposal request.

One proposal is limited to one trip and at most 25 operations. Each operation
uses a strict discriminated DTO, rejects extra fields, and references only
opaque handles supplied in the same trip-scoped context. The server maps
handles to owner-scoped records. It never accepts arbitrary record IDs, free
text place names, notes, reservation edits, place edits, or a generic patch
language. Operations run in their listed order against a private immutable
projection. Repeating an operation on an existing target is allowed only when
each step is valid against the state produced by the preceding step; a target
removed earlier in the list cannot be referenced again.

## Protected records and removals

Items with status `booked` or `completed` are protected from moves, time
changes, and removal. An item linked to a `confirmed` reservation is also a
protected anchor for moves and time changes. The proposal cannot change or
remove reservations, confirmation details, candidate notes, item status, item
notes, links, or shared places. An item can enter the removable allowlist only
when the traveler explicitly selected it and it has no reservation link and
status is neither `booked` nor `completed`. The server derives and validates
that allowlist; model output cannot grant itself removal permission.

Existing candidates and all shared places used by the itinerary, reservations,
or candidate set are represented by opaque handles in the bounded internal
projection. Proposed additions require a candidate handle for the same trip.
No user prompt, private note, confirmation code, reservation source reference,
or raw provider payload is part of the future external context.

## Revisions and no-op policy

Trips and shared places each have a nonnegative, monotonically increasing
integer `revision`, initialized to zero. Existing `updated_at` timestamps are
not concurrency tokens. Trip revision covers trip fields, date/timezone
reconciliation, trip days, itinerary items and ordering, reservations, saved
candidate links/notes, manual candidate creation, and provider candidate import.
Shared place revision covers independent edits to that place's metadata. A
place edit does not fan out revision changes or locks to referencing trips.

All existing trip mutations accept an optional `X-Expected-Revision` header
for backward compatibility. Independent place edits accept the same header
against the place revision. When supplied, the expected value is compared
after the trip/place row is locked and before any domain mutation in that same
transaction. A mismatch returns stable HTTP 409 code `stale_revision`, with
the expected and current revisions; it performs no write. Omitted headers
retain legacy last-writer behavior but still advance revisions for actual
changes.

Revision increments once per committed request when an existing aggregate's
authoritative value or relationship set changes. A new trip or place starts at
revision zero; creating a child item, reservation, or candidate advances the
trip revision. Deleting the root trip checks its revision and removes the
aggregate, so no successor revision is retained. A patch whose normalized
values equal the stored values, an empty patch, a move that leaves the same
order/date/schedule, and a repeated provider import that adds no candidate are
no-ops and do not advance a revision. A failed transaction, failed
precondition, or rejected domain change does not advance a revision. Place
creation starts at revision zero; creating a manual/provider candidate advances
only the owning trip revision for the new candidate relationship.

## Dependency footprint, preview, expiry, and replay

A future stored proposal captures the trip revision and the revisions of every
shared place referenced by its itinerary/reservations/candidates or by an
operation. It must capture candidate/reservation scope through the trip
revision, not by duplicating those records' timestamps. Apply acquires the trip
root lock first, then shared place locks in ascending UUID order, and compares
the complete footprint before any mutation. Editing one shared place invalidates
only proposals whose footprint includes that place.

Preview and apply use the same concrete travel rules as manual edits: inclusive
trip days, insertion positions, destination-day wall-clock preservation,
timezone validation, DST gap/fold rejection, confirmed/booked protection,
and reservation-overlap warnings. Preview includes every day's full final
ordered item list and local schedule, plus deterministic warnings. There is no
silent rebase. A later accepted upstream capability must return an explicit
support mode. `research_evidence` proposals require verified citations for
every operation; insufficient, expired, or uncited outcomes cannot become
proposals. A `context_only` proposal is permitted only when no research sessions
were supplied and may rely only on the traveler instruction and typed current
itinerary projection. It cannot claim current external facts such as opening
hours or availability. A supplied evidence lookup that fails validation must
remain insufficient or expired and cannot fall back to context-only policy.
Both modes still pass through this application's deterministic preview and
revalidation.

The future proposal lifetime is at most 24 hours from creation and is shortened
to the earliest referenced evidence expiry. It cannot be extended by replay.
Idempotency is scoped to owner and trip: replay with the same normalized
request returns the original proposal; reusing a key with a different request
conflicts. Apply is explicit and atomic with the itinerary changes, terminal
proposal state, resulting revision, and minimal replayable outcome. A repeated
apply returns that stored outcome without executing again, even after expiry.
An expired or stale ready proposal cannot apply; no automatic retry or rebase
is permitted. Proposal rows are owned by and cascade with the trip so deletion
does not retain its itinerary snapshot or private context.

These lifecycle rules are requirements for a future accepted-contract stage;
this ADR does not implement or expose that lifecycle.

## Consequences

- Manual CRUD remains available with the AI gate disabled.
- Revisions make supplied cross-tab preconditions useful while retaining
  compatibility with older clients.
- Reusable place edits are accounted for without cross-trip lock fanout.
- Local strict DTO and preview code can be tested without representing itself
  as an accepted personal-ai-system wire contract.
- Proposal generation, storage, apply/replay, UI, auth, and deployment remain
  unimplemented until their independent prerequisites and acceptance evidence
  exist.
