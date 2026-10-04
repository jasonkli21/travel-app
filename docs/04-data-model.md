# Data model

Status: Phase 5 proposal lifecycle implemented locally; independent review pending, gates off
Date: 2026-10-04

The initial migration implements the core itinerary graph. Phase 1 adds
application services and ordering constraints; Phase 2 adds manual reservations,
trip-scoped saved-place candidates, richer place metadata, reservation links,
and retained source attribution for provider-imported places. Phase 4 stores
manual candidates in these existing tables; AI sessions and evidence remain
owned by `personal-ai-system` and are not copied into this database.
Attachments remain planned rather than implemented.

## Implemented scaffold tables

### `trips`

```text
id UUID PK
owner_id
title
start_date
end_date
timezone
revision INTEGER NOT NULL DEFAULT 0 CHECK revision >= 0
created_at
updated_at
```

Constraint: `start_date <= end_date`.

Trip and itinerary services enforce owner scoping and ensure trip-day dates
match the trip range.

### `trip_days`

```text
id UUID PK
trip_id FK -> trips
day_index
date
title?
created_at
updated_at
```

Constraints:

- `(trip_id, day_index)` unique,
- `(trip_id, date)` unique.

### `places`

```text
id UUID PK
owner_id
revision INTEGER NOT NULL DEFAULT 0 CHECK revision >= 0
name
latitude?
longitude?
address?
category?
phone?
website_url?
provider?
provider_place_id?
provider_source_name?
provider_source_attribution?
provider_source_license?
provider_source_url?
created_at
updated_at
```

External provider identity and source attribution are optional. Phase 3 stores
the source attribution, license, and source link returned for a provider-backed
place so the required credit stays available in search results and saved app
records.

Migration `0005` checks that coordinates are both null or both present, with
latitude in [-90, 90] and longitude in [-180, 180]. Coordinates are validated
at both the HTTP and SQL boundaries.

### `itinerary_items`

```text
id UUID PK
trip_day_id FK -> trip_days
place_id? FK -> places
reservation_id? FK -> reservations
item_type
title
notes?
starts_at?
ends_at?
sort_order
status
created_at
updated_at
```

Current scaffold enums are represented as checked strings to keep migration behavior explicit.

## Implemented Phase 2 tables

### `reservations`

Normalized manual reservation fields:

```text
id
owner_id
trip_id
reservation_type
status
provider_name
confirmation_code?
starts_at?
ends_at?
place_id?
source_reference?
notes?
created_at
updated_at
```

`reservation_type` is one of `lodging`, `flight`, `train`, `car_rental`,
`activity`, `dining`, or `other`. `status` is `tentative`, `confirmed`, or
`cancelled`. Scheduled values are stored as timezone-aware instants; the API
round-trips local date/time fields in the owning trip timezone. An unscheduled
reservation has all four local schedule fields null and does not produce a
conflict warning.

An itinerary item links to at most one reservation through `reservation_id`; a
reservation may anchor multiple itinerary items. The service validates that
both records belong to the same owner and trip. Deleting a reservation clears
item links.

### `saved_places`

```text
id UUID PK
owner_id
trip_id FK -> trips
place_id FK -> places
note?
created_at
updated_at
```

The `(owner_id, trip_id, place_id)` pair is unique. Saving/removing the
relationship does not mutate or delete the reusable owner-scoped place.

Phase 4 manual research candidates create the `places` row and `saved_places`
relationship in one transaction. Research session IDs, questions, answers,
citations, and evidence have no travel-side table or migration.

## Planned tables, not implemented

### `attachments`

```text
id
owner_id
trip_id
reservation_id?
object_key
media_type
size_bytes
sha256
original_filename
created_at
```

Blob bytes live outside Postgres.

## Implemented Phase 5 storage

### `itinerary_proposals` — migrations `0007` and `0008`

Travel owns the durable proposal lifecycle. The trip foreign key cascades on
deletion; `(owner_id, trip_id, idempotency_key)` is unique. An
`(owner_id, trip_id, created_at)` index supports scoped record ordering.
Portable JSON columns contain only the
validated bounded projection, operations, full preview, citation metadata, and
minimal replay outcome.

```text
id UUID PK
owner_id, trip_id FK -> trips ON DELETE CASCADE
idempotency_key, deterministic downstream_key
request_fingerprint SHA-256
state: generating | outcome_unknown | ready | failed | applied | rejected
schema_version, policy_version, support_mode, upstream_revision, opaque trip_handle
upstream_proposal_id?, generation_deadline
base_trip_revision, base_place_revisions JSON, base_snapshot JSON
operations JSON?, operation_support JSON, preview JSON?, citations JSON
expires_at?, failure_code?
applied_outcome JSON?, applied_at?, rejected_at?
created_at, updated_at
```

Migration `0008` backfills pre-existing proposal rows with upstream revision
`8535cad3a146b1a19cab0958c439f170d19b8095` and empty `operation_support`, then
enforces both fields as non-null. New rows record the exact accepted upstream
revision and operation-to-evidence references for later audit. Checks constrain
lifecycle/support states and nonnegative base revisions. The
base snapshot is an internal mapping for revalidation; it carries itinerary and
candidate identities/labels, local schedules, place revisions, and only the
reservation status/timing needed to protect anchors and calculate conflicts.
It excludes reservation provider names, notes, confirmations and source
references. Raw instructions are sent only to the accepted upstream client and
persist as a one-way request fingerprint; provider response bodies are never
stored. Trip deletion removes proposal snapshots/outcomes with `ON DELETE
CASCADE`.

Applied outcomes are immutable and replayed exactly on later apply calls,
including when the proposal would otherwise be stale or expired. `stale` and
`expired` are presentation states derived from the current trip/place footprint
and expiry; they are not stored lifecycle values. Records remain available
while the trip exists; no independent proposal-retention worker is implemented.

## Ordering

`sort_order` is a contiguous, zero-based integer within each day in Phase 1 and
remains unchanged by Phase 2 reservation/candidate operations.
Create appends, delete compacts, and move removes/reinserts and renumbers both
affected days in one transaction. The database rejects negative values and the
service validates destination positions.

Migration `0005` adds unique `(trip_day_id, sort_order)`. Reorders and moves
flush distinct temporary positions above occupied/final positions before
compacting, inside the same locked transaction. This avoids transient unique
violations without deferred constraints or PostgreSQL-specific triggers.

`day_index` is a contiguous, one-based integer within a trip. Trip creation
generates one day for every inclusive calendar date. Date-range edits preserve
overlapping day IDs and titles, add new dates, and reject removal of a day that
still contains itinerary items.

Mutation services lock the trip aggregate row while calculating and renumbering
items, so concurrent appends, deletes, and moves cannot reuse one snapshot's
order values. Integer ordering plus transactional renumbering is sufficient for
the personal single-user product without introducing fractional indexes or
collaboration machinery.

Multi-query aggregate reads take a shared root lock; mutations take an
exclusive root lock and refresh existing ORM collections. Reusable place
metadata is independently locked. Writes may provide the aggregate's current
revision with `X-Expected-Revision`; a supplied stale value returns 409 before
mutation. Omitted headers preserve legacy last-writer compatibility while
actual changes still advance revisions. Editing a shared place advances only
its own revision and never fans out trip updates.

Do not introduce fractional indexing/CRDTs without a real collaboration requirement.

## Time

Store timestamps as timezone-aware values; the application should normalize server-generated timestamps consistently.

Trips also have an IANA timezone string for display/local scheduling.

Future itinerary operations must distinguish:

- absolute instant,
- local wall-clock time,
- date-only/flexible item.

Do not silently convert a date-only plan into a fixed UTC instant.

Phase 1 accepts local `HH:MM` values relative to the owning trip day and IANA
timezone. Timed items are persisted as timezone-aware instants and rendered back
as local times. Daylight-saving gaps and ambiguous folds are rejected, as are
cross-midnight item ranges. Date-only items keep both time values null.

A cross-day move preserves each local HH:MM endpoint on the destination date.
If either endpoint lands in a DST gap/fold, neither schedule nor order changes.
Migration `0005` repairs the old source-date timestamp bug and normalizes legacy
order. It requires online inspection, a backup and stopped writes; unresolved
DST or invalid-coordinate data stops the migration transactionally. Downgrade
removes constraints but cannot undo data repair; see the recovery guide.

Phase 2 reservation schedules use local date/time pairs in the owning trip
timezone. A reservation may cross midnight, but a scheduled value must have a
start date/time and an end date requires an end time. DST gaps and folds are
rejected. A single supplied endpoint is a point event; both missing endpoints
are unscheduled. Conflict calculation compares the resulting instants and
excludes the intentionally linked item, cancelled records, and boundary-only
equality. Changing a trip timezone preserves each reservation endpoint's local
date and wall-clock value by re-resolving it in the new zone. Reservation
schedule PATCHes are atomic: all four local schedule fields must be supplied
together, and four nulls clear the schedule.

## Places and geospatial behavior

Store latitude/longitude as ordinary numeric columns initially.

Do not require PostGIS in Phase 1, Phase 2, or Phase 3.

Phase 3 migration `0004` adds nullable provider source name, attribution,
license, and URL fields to `places`. This is a reversible additive change;
manual places keep these fields null.

If later workloads genuinely require server-side spatial queries, add a dedicated ADR and revisit AWS/Aurora DSQL portability.

## Deletion

Initial FK behavior:

- deleting a trip cascades trip days, itinerary items, reservations, and saved-place relationships,
- deleting a day cascades its itinerary items,
- deleting a place sets itinerary `place_id` and reservation `place_id` to null
  and removes saved-place relationships.

Phase 1 and Phase 2 make trip, itinerary-item, and reservation deletion
permanent. The API requires an
explicit confirmation in the UI before deleting a trip; the existing database
cascades remove its days/items, and an item delete compacts its day's order.
Cancelled reservations remain as manual history, while soft deletion, archival,
and audited deletion events remain future product decisions for later entities.

The database cascade is therefore an implementation detail of the Phase 1/2
permanent-delete policy, not a general rule for future product entities.
