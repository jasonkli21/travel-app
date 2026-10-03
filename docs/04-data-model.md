# Data model

Status: initial relational vocabulary  
Date: 2026-10-02

The initial migration implements the core itinerary graph. Phase 1 adds
application services and an additive ordering-constraint migration around that
schema; reservations, attachments, saved places, and AI proposals remain
planned rather than implemented.

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

These values may evolve before Phase 1 is considered stable.

## Planned tables, not implemented

### `reservations`

Likely normalized base reservation fields:

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
structured_details JSON?
```

Avoid creating one giant unvalidated JSON booking document. Use JSON only for provider/type-specific tail data after stable common fields are modeled.

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

### `saved_places`

May either be:

- a trip/place join table with state/notes, or
- a first-class candidate entity if research workflows require more metadata.

Delay the choice until the saved-place UX is designed.

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

`sort_order` is a contiguous, zero-based integer within each day in Phase 1.
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
cross-midnight ranges. Date-only items keep both time values null.

## Places and geospatial behavior

Store latitude/longitude as ordinary numeric columns initially.

Do not require PostGIS in Phase 1.

If later workloads genuinely require server-side spatial queries, add a dedicated ADR and revisit AWS/Aurora DSQL portability.

## Deletion

Initial FK behavior:

- deleting a trip cascades trip days and itinerary items,
- deleting a day cascades its itinerary items,
- deleting a place sets itinerary `place_id` to null.

Phase 1 makes trip and itinerary-item deletion permanent. The API requires an
explicit confirmation in the UI before deleting a trip; the existing database
cascades remove its days/items, and an item delete compacts its day's order.
Soft deletion, archival, and audited deletion events remain future product
decisions for later entities.

The database cascade is therefore an implementation detail of the Phase 1
permanent-delete policy, not a general rule for future product entities.
