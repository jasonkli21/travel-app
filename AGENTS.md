# Agent instructions

This repository is developed iteratively with Codex and human review.

## Start here

1. Read [the documentation router](docs/README.md).
2. Read [the current state](docs/current-state.md).
3. Follow the router for the task; read a phase plan only when implementing that phase.

Code, executable contracts, migrations, and tests define current behavior.
Release/review records describe what was exercised. ADRs record accepted
decisions. Product and architecture documents describe intent. Plans authorize
no work by themselves; historical documents are context only.

## Durable boundaries

- Travel owns canonical trips, itinerary, reservations, saved places, user
  edits, and attachments.
- Access `personal-ai-system` through typed HTTP clients. Never import its
  Python packages into this repository.
- PostgreSQL, relational constraints, transactions, and Alembic migrations
  protect travel state. Keep core SQL portable where practical.
- Enforce hard constraints and authorization deterministically in application
  code and the database.
- AI output remains advisory until typed, validated, previewed, and explicitly
  applied through travel services. External evidence is time-bound, not a
  permanent app fact.
- Keep local development first-class. Private data requires verified identity
  and private storage; fail closed when a gate is absent.
- Do not add platform abstractions before a concrete product need justifies
  them. A plan's existence does not authorize implementation.

## Engineering conventions

Backend: Python 3.12+, FastAPI/Pydantic, SQLAlchemy 2, Alembic, Ruff, mypy,
pytest. Keep route handlers thin; put workflows in services/repositories.

Frontend: strict TypeScript and Next.js App Router. Keep domain access in typed
client modules/hooks, and prefer accessible responsive components over new
state-management dependencies.

Every schema change needs a migration. Changes to behavior or contracts should
include focused tests and documentation; add an ADR for a durable architectural
decision. Do not claim checks that were not run. Inspect and back up existing
data before an online legacy-data repair.

At phase closeout, update only `docs/current-state.md` and the relevant release
or review evidence. Change the root README only when user-facing setup,
runtime, or capability facts change. Cloud deployment and external/private
feature enablement require separate authorization and verification.
