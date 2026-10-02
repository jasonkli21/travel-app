# `personal-ai-system` integration

Status: boundary accepted; feature contracts deferred  
Date: 2026-10-02

## Rule

`personal-travel-app` is a client of `personal-ai-system`.

Do not import `personal_ai.*` Python modules or couple this repository to its Firestore schemas.

```text
travel-api
   |
   | typed HTTP
   v
personal-ai-system
```

## Why

The two systems have different responsibilities and persistence models.

Travel app:

- authoritative trip state,
- deterministic itinerary operations,
- user edits,
- reservations/documents.

Personal AI:

- conversations/context,
- memory,
- research/evidence,
- model providers,
- shared research/entity/ranking/evaluation behavior.

An HTTP boundary allows both repositories to evolve independently.

## Current scaffold

`PersonalAIClient` implements only a health probe.

That is intentional.

The current `personal-ai-system` repository's accepted research architecture is still evolving. Do not invent a private cross-repo research API in this scaffold and force the AI project to support it later.

## Future contract categories

### Research

Travel sends bounded context and a typed travel research request.

Example conceptual input:

```json
{
  "kind": "travel.restaurant_research",
  "trip_id": "local-travel-id",
  "constraints": {
    "near": {"latitude": 13.7, "longitude": 100.5},
    "date": "2026-12-20",
    "meal": "dinner"
  }
}
```

The AI system returns evidence-grounded candidates.

The travel app may persist a saved candidate/reference but should not blindly copy all external evidence into its authoritative state.

### Extraction

Conceptual flow:

```text
user/email connector supplies document
 -> personal-ai-system extracts typed candidate
 -> travel-api validates
 -> UI previews
 -> user accepts
 -> travel-api writes reservation
```

Do not let extraction write travel tables directly.

### Proposed actions

Future AI mutation output must be a versioned typed structure.

Example:

```json
{
  "kind": "itinerary_patch",
  "version": 1,
  "operations": [
    {
      "op": "move_item",
      "item_id": "uuid",
      "target_day_id": "uuid",
      "starts_at": "2026-12-19T10:00:00+07:00"
    }
  ]
}
```

Before applying:

1. validate schema;
2. verify ownership;
3. check referenced objects still exist;
4. re-check hard constraints;
5. detect stale input/current-state mismatch;
6. apply inside a transaction;
7. return authoritative records.

## Identity/authentication

The current AI repo's `owner_id=local` seam is not authenticated identity.

The travel app should use the same local-only concept for bootstrap compatibility, but cross-service cloud integration with real private data requires authenticated service/user identity.

Do not solve this with a shared hard-coded owner string in production.

## Memory

Travel should not replicate the AI memory store.

It may send current authoritative trip context and ask `personal-ai-system` to retrieve relevant travel/global preferences.

Memory is advisory.

Current explicit trip state wins.

Fresh external evidence wins over stale remembered observations.

## Evidence

Travel should display source/freshness metadata returned by the AI system.

External observations such as price, opening hours, availability, and travel time must not be persisted as timeless facts.

## Failure behavior

AI integration should degrade independently.

Manual itinerary CRUD must still work if `personal-ai-system` is unavailable, rate-limited, misconfigured, or disabled.

No travel transaction should require a successful AI call unless the user explicitly invoked an AI-only feature.

## Streaming

Use ordinary HTTP/SSE where the accepted AI endpoint provides streaming.

Do not add WebSockets or a travel-owned Pub/Sub pipeline merely because AI work can stream.

Long-running asynchronous integration should be revisited only when a concrete use case exceeds request-owned execution.
