# Product design

Status: initial design direction  
Date: 2026-10-02

## Design goals

1. **Structured first.** The itinerary is the primary interaction surface.
2. **Low-friction editing.** Manual edits should always be easier than asking AI for trivial changes.
3. **AI beside the workflow.** Research/assistant UI complements the structured workspace.
4. **Evidence is inspectable.** Research recommendations should expose sources/freshness.
5. **Committed vs. flexible plans are visible.** Travel planning often includes both hard reservations and optional ideas.
6. **Mobile responsive, desktop optimized.** Itinerary editing and research benefit from desktop space, but the plan must remain readable on a phone during travel.

## Primary navigation

Proposed initial information architecture:

```text
Trips
  -> Trip
      -> Overview
      -> Itinerary
      -> Map
      -> Reservations
      -> Saved places
      -> Documents
```

Research may begin as a right-side panel rather than a top-level route.

## Itinerary surface

Each day should eventually show:

- date/location,
- ordered items,
- time or time range,
- place,
- booked/tentative status,
- notes,
- travel/logistics metadata,
- warnings/conflicts,
- source/booking link where applicable.

Direct actions:

- add item,
- edit,
- move,
- reorder,
- duplicate,
- delete/cancel,
- mark flexible/committed.

## Committed vs. flexible planning

This should be represented structurally rather than only in notes.

A future item may have fields such as:

```text
planning_mode:
  committed
  preferred
  optional
```

Do not add the field until product behavior is defined, but preserve the concept in design work.

## AI panel

The AI panel should eventually understand the active trip/day selection.

Possible modes:

```text
Ask
Research
Compare
Propose changes
```

It should never imply that a proposed change has been applied until the travel API confirms the mutation.

## Proposed-change UX

A future itinerary patch should render as a diff:

```text
Move "Museum"
from: Friday 14:00
to:   Saturday 10:00

Reason:
Reduces Friday travel and fits Saturday opening hours.

[Apply] [Dismiss]
```

Application services revalidate the mutation at apply time.

## Research candidate UX

Research results should distinguish:

- candidate entity,
- hard-constraint fit,
- soft rationale,
- source evidence,
- freshness,
- saved/added state.

Avoid a single opaque "AI score" as the only explanation.

## Maps

Maps are valuable but not Phase 1-critical.

Initial place records store latitude/longitude and external-provider identity when available. A map provider should be selected in a later ADR after free-tier/pricing/privacy evaluation.

## Visual direction

The scaffold uses a restrained travel-workspace layout:

- warm neutral background,
- strong content hierarchy,
- dense enough for planning but not spreadsheet-like,
- itinerary center stage,
- AI/research visually secondary.

The scaffold's CSS is a placeholder, not a finished design system.
