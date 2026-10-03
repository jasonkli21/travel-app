# Data model

Status: Phase 2 relational vocabulary delivered locally
Date: 2026-10-02

The initial migration implements the core itinerary graph. Phase 1 adds
application services and ordering constraints; Phase 2 adds manual reservations,
trip-scoped saved-place candidates, richer place metadata, and reservation links.
Attachments and AI proposals remain planned rather than implemented.

## Implemented scaffold tables

### `trips`

```text
id UUID PK
owner_id
title
start_date
end_date
timezone
created_at
updated_at
```

Constraint: `start_date <= end_date`.

Phase 1 service invariants still need to ensure owner scoping and that trip-day dates match the trip range.

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
name
latitude?
longitude?
address?
category?
phone?
website_url?
provider?
provider_place_id?
created_at
updated_at
```

External provider identity is optional.

No map/place provider is selected yet.

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

### `ai_proposals`

Do not create this table until the proposal lifecycle is defined.

Potential need:

```text
proposal id
trip id
proposal kind/version
input snapshot/version
typed operations
evidence references
status
created_at/applied_at
```

The requirement is auditability and stale-proposal detection, not storing arbitrary model prose.

## Ordering

`sort_order` is a contiguous, zero-based integer within each day in Phase 1 and
remains unchanged by Phase 2 reservation/candidate operations.
Create appends, delete compacts, and move removes/reinserts and renumbers both
affected days in one transaction. The database rejects negative values and the
service validates destination positions.

`day_index` is a contiguous, one-based integer within a trip. Trip creation
generates one day for every inclusive calendar date. Date-range edits preserve
overlapping day IDs and titles, add new dates, and reject removal of a day that
still contains itinerary items.

Mutation services lock the trip aggregate row while calculating and renumbering
items, so concurrent appends, deletes, and moves cannot reuse one snapshot's
order values. Integer ordering plus transactional renumbering is sufficient for
the personal single-user product without introducing fractional indexes or
collaboration machinery.

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

Phase 2 reservation schedules use local date/time pairs in the owning trip
timezone. A reservation may cross midnight, but a scheduled value must have a
start date/time and an end date requires an end time. DST gaps and folds are
rejected. Conflict calculation compares the resulting instants and excludes
the intentionally linked item, cancelled records, and boundary-only equality.

## Places and geospatial behavior

Store latitude/longitude as ordinary numeric columns initially.

Do not require PostGIS in Phase 1 or Phase 2.

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

The database cascade is therefore an implementation detail of the Phase 1
permanent-delete policy, not a general rule for future product entities.
