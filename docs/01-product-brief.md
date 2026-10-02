# Product brief

Status: initial product direction  
Date: 2026-10-02

## Purpose

Build a personal travel application that is useful as a real planner even when AI is unavailable, while integrating deeply with `personal-ai-system` for research, memory-aware assistance, structured extraction, and proposed changes.

This is a separate Git repository from `personal-ai-system`.

## Product principle

> Travel state belongs to the travel application. Intelligence is shared through `personal-ai-system`.

The application should not become "a chatbot with a trip skin." Its primary surfaces are structured travel workflows:

- day-by-day itinerary,
- places and maps,
- reservations,
- travel logistics,
- documents,
- research and comparison views.

AI should make those workflows easier to populate, research, reorganize, and understand.

## Initial user

Single personal owner.

The initial schema retains an `owner_id` seam so authenticated identity can replace the local owner later without a data-model redesign.

The local-owner seam is **not authentication**.

## Long-term user experience

A trip workspace may include:

```text
+----------------+------------------------------+----------------------+
| Trip navigation| Day-by-day itinerary         | AI / research        |
|                |                              |                      |
| Overview       | Dec 9 - Chiang Mai           | Ask about this trip  |
| Itinerary      | 09:00 coffee                 | Research candidates  |
| Map            | 11:00 Old City               | Proposed changes     |
| Reservations   | 18:00 night market           | Evidence / sources   |
| Saved places   |                              |                      |
| Documents      | Dec 10 ...                   |                      |
+----------------+------------------------------+----------------------+
```

The structured itinerary remains editable directly without AI.

## Core product objects

- Trip
- Trip day
- Itinerary item
- Place
- Reservation
- Saved place
- Note
- Attachment/document
- Research result/candidate reference
- Proposed AI action/change set

Not all objects are Phase 1 implementation scope.

## AI-assisted capabilities over time

### Research

Examples:

- neighborhoods that fit current trip constraints/preferences,
- hotels,
- restaurants near a day's itinerary,
- activities,
- transportation options,
- comparisons with current evidence.

### Structured extraction

Examples:

- hotel confirmation email -> candidate reservation,
- flight email -> candidate transport/reservation,
- uploaded booking PDF -> candidate structured record.

Extraction creates a proposal. Travel-domain validation and user approval create the authoritative record.

### Itinerary reasoning

Examples:

- "This day is overloaded."
- "Move the museum to Saturday morning."
- "Minimize backtracking."
- "Keep one afternoon intentionally loose."

AI should return a typed proposed patch rather than requiring the UI to parse prose.

### Memory-aware research

The travel app may ask `personal-ai-system` to apply relevant travel/global preferences during research.

Memory must not override current hard constraints or fresh evidence.

## Phase 1 product scope

Phase 1 should produce a usable non-AI itinerary planner:

- create/list/open trips,
- auto-create or manage trip days,
- create/edit/delete/reorder itinerary items,
- optionally attach a place to an itinerary item,
- basic responsive itinerary UI,
- local persistence in PostgreSQL,
- API/UI validation and tests.

AI-system integration remains a health/client boundary until a stable research/extraction API exists.

## Explicit non-goals for early phases

- Gmail ingestion,
- live hotel/flight booking,
- automatic purchases/reservations,
- broad autonomous agents,
- offline mobile app,
- collaborative/multi-user trips,
- social sharing,
- real-time multi-user editing,
- PostGIS,
- generic plugin architecture,
- app-owned LLM/memory implementation.

## Success criteria

The application becomes valuable incrementally:

1. It is a good manual itinerary planner.
2. It can display and persist structured travel state reliably.
3. It can call the shared AI system without duplicating its intelligence stack.
4. AI output remains grounded, typed where it affects app state, and reversible/previewable.
