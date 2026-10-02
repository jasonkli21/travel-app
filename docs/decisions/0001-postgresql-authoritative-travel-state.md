# ADR 0001 — PostgreSQL for authoritative travel state

Status: accepted  
Date: 2026-10-02

## Context

Trips, days, itinerary items, places, reservations, and attachments form a relational, mutable application model.

The app needs local-first development and a credible free cloud path.

## Decision

Use PostgreSQL 16 locally and target a managed PostgreSQL-compatible cloud database initially.

Use SQLAlchemy 2 and Alembic.

Keep core schema within a portable PostgreSQL subset unless a later ADR accepts provider lock-in.

## Consequences

- relational invariants can be enforced naturally;
- local/cloud parity is high with Neon;
- Firestore/DynamoDB are not primary travel stores;
- an eventual Aurora DSQL experiment remains plausible but is not guaranteed drop-in compatibility.
