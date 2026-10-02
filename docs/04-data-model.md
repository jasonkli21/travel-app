# Data model

Status: initial relational vocabulary  
Date: 2026-10-02

The initial migration intentionally implements only the core itinerary graph.

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

`sort_order` is intentionally simple in the scaffold.

Phase 1 should choose an ordering strategy and test move behavior.

For a personal single-user app, integer ordering plus transactional renumbering is probably sufficient initially.

Do not introduce fractional indexing/CRDTs without a real collaboration requirement.

## Time

Store timestamps as timezone-aware values; the application should normalize server-generated timestamps consistently.

Trips also have an IANA timezone string for display/local scheduling.

Future itinerary operations must distinguish:

- absolute instant,
- local wall-clock time,
- date-only/flexible item.

Do not silently convert a date-only plan into a fixed UTC instant.

## Places and geospatial behavior

Store latitude/longitude as ordinary numeric columns initially.

Do not require PostGIS in Phase 1.

If later workloads genuinely require server-side spatial queries, add a dedicated ADR and revisit AWS/Aurora DSQL portability.

## Deletion

Initial FK behavior:

- deleting a trip cascades trip days and itinerary items,
- deleting a day cascades its itinerary items,
- deleting a place sets itinerary `place_id` to null.

Before user-facing destructive actions are implemented, decide whether product deletion should be hard delete, soft delete/archive, or an audited event.

Do not infer that database cascade equals final product deletion policy.
