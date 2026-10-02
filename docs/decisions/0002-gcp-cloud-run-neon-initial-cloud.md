# ADR 0002 — GCP Cloud Run + Neon as the initial cloud direction

Status: accepted for architecture; deployment deferred  
Date: 2026-10-02

## Context

The app should later run cheaply/free at personal scale.

`personal-ai-system` already uses Cloud Run, while travel's relational model favors PostgreSQL.

## Decision

Target:

- Cloud Run for Next.js and FastAPI;
- Neon Postgres as the initial managed cloud database;
- GCS when blob storage is required.

Do not implement cloud deployment during Phase 0/1 without separate authorization.

## Consequences

- compute operations align with the AI system;
- database remains actual Postgres;
- the app crosses GCP -> Neon network boundary;
- deployment depends on third-party free-tier terms;
- AWS remains an optional later migration/learning path.
