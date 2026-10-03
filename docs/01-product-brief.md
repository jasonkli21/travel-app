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

## Phase 2 product scope

Phase 2 extends the manual planner with booked anchors and optional candidates:

- record tentative, confirmed, and cancelled reservations manually;
- link a reservation to one or more itinerary items through the trip domain;
- surface deterministic overlap warnings without automatically changing plans;
- maintain richer manual place metadata and save a place as a trip candidate.

External booking import, AI research, and authentication remain outside this
phase.

## Phase 3 product scope

Phase 3 adds map context and manual-first location workflows:

- show geocoded itinerary, reservation, and saved-candidate places on a trip map;
- let the user submit a place/address search and explicitly save a selected
  result as a trip candidate;
- estimate travel time between consecutive scheduled, located itinerary items
  for one day and flag tight transfers using a deterministic buffer rule;
- retain provider-source attribution for imported places and keep route
  estimates ephemeral.

Provider observations remain advisory and never silently change the itinerary.
The trip application remains usable without provider keys.

## Phase 4 product scope delivered locally

Phase 4 adds manual-first, evidence-grounded research inside a trip workspace:

- ask a question about one selected trip day through the accepted
  `personal-ai-system` `research-v1` HTTP contract;
- display only validated completed answers and their citations, observation
  times, and expiry times;
- keep the query and bounded day context in the configured AI/search boundary,
  with a visible notice explaining that transfer;
- create a trip candidate only from fields the traveler enters and confirms;
- keep all AI output out of authoritative itinerary and reservation state.

The travel-side research gate defaults off. The local owner seam is still not
authentication, and real private integrations remain out of scope.

## Explicit non-goals for early phases

- Gmail ingestion,
- live hotel/flight booking,
- automatic booking imports, purchases, or reservation monitoring,
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
