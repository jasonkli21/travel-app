# ADR 0003 — Integrate with `personal-ai-system` over HTTP

Status: accepted  
Date: 2026-10-02

## Context

The travel app and AI system have independent repositories, storage models, release cycles, and responsibilities.

Sharing Python packages/internal Firestore schemas would couple them tightly.

## Decision

All runtime integration uses an explicit versioned network API through a typed `PersonalAIClient`.

The scaffold only implements health.

Research, extraction, and action contracts are added only after accepted APIs exist on the AI side.

## Consequences

- either repository can change internals independently;
- local development can point to a local AI service;
- AI unavailability can degrade independently from manual travel CRUD;
- cross-service authentication becomes required before real cloud personal data.
