# ADR 0005 — Defer blob and map providers

Status: accepted  
Date: 2026-10-02

## Context

Travel will likely need attachments and maps, but neither is necessary for the first manual itinerary vertical slice.

Choosing providers now would add cost/terms/configuration decisions before requirements are concrete.

## Decision

- Do not add a blob service during Phase 0/1.
- Do not add a map/place provider during Phase 0/1.
- Store optional latitude/longitude/provider IDs in the place model.
- Add a `BlobStore` boundary only when the first attachment feature is implemented.

## Consequences

- Phase 1 remains credential-free;
- GCS is the expected cloud blob candidate but not a current dependency;
- map/provider retention and attribution rules are evaluated in a later ADR.
