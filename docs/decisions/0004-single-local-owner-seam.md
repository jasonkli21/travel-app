# ADR 0004 — Single local owner seam during bootstrap

Status: accepted  
Date: 2026-10-02

## Context

The first product is for one personal user, but authentication is not part of Phase 0/1.

Future cloud/private data must not require a complete schema redesign.

## Decision

Persist an `owner_id` on owner-scoped records and use `local` during local bootstrap.

Repository/service methods must scope operations by owner.

`local` is not authenticated identity.

## Consequences

- Phase 1 stays simple;
- future verified identity can replace the local owner source;
- a public deployment cannot be treated as secure merely because records contain `owner_id`.
