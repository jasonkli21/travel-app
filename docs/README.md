# Documentation router

For an ordinary task, read this page, then [current state](current-state.md),
then only the sources routed below.

## Reading hierarchy

Use these documents in this order to understand project context:

1. [`current-state.md`](current-state.md) is the single living status snapshot
   for factual current implementation, acceptance status, and open gates.
2. Architecture documents state durable responsibilities and invariants.
3. ADRs explain decisions in their historical context and record supersession.
4. Phase plans record historical implementation intent and constraints; they
   are not current status or authorization.
5. Release documents describe what a phase delivered and what was exercised.

This is a reading hierarchy, not a substitute for executable evidence. Code,
schemas/contracts, migrations, and tests define behavior. When a status summary
conflicts with implementation or release evidence, follow the evidence and
update `current-state.md`. Keep historical plans intact rather than rewriting
them as current architecture. Run `make docs-check` to verify local Markdown
link targets. The root `README.md` is for product users and local setup.

## Authority for claims

When sources disagree about behavior or delivery, use this order:

1. Code, executable schemas/contracts, migrations, and tests define behavior.
2. Current release, review, and verification records show what was implemented
   and exercised.
3. Applicable ADRs record accepted decisions and supersession history.
4. Product and architecture documents describe durable boundaries and scope.
5. Phase plans describe historical or active intent, not delivery or
   authorization.
6. Historical and superseded documents provide context only.

## Route by task

| Task | Read |
| --- | --- |
| Itinerary, reservation, or place domain | [Product brief](01-product-brief.md), [data model](04-data-model.md), [service workflow map](../backend/src/personal_travel/services/README.md), and relevant ADR |
| Database, schema, or migration | [Data model](04-data-model.md), relevant ADR, and [local development](07-local-development.md) migration guidance |
| Personal AI integration or proposal lifecycle | [AI integration](06-ai-integration.md), relevant contract/ADR and release evidence, [current state](current-state.md) |
| Authentication or private sources | Phase 6 plan, [ADR 0011](decisions/0011-phase6-google-identity-and-ai-auth.md), [ADR 0012](decisions/0012-phase6-private-source-storage.md), [ADR 0013](decisions/0013-phase6-booking-document-import.md), and [Phase 6 evidence](releases/phase-6-booking-imports.md) |
| Attachments, exports, or travel mode | ADR 0015, [Phase 8 plan](phase-8-implementation-plan.md), and [Phase 8 evidence](releases/phase-8-attachments-exports.md) |
| Frontend UX | [Product design](02-product-design.md) and the relevant feature contract or release record |
| Cloud or deployment | [Technology choices](05-technology-choices.md), [cloud deployment](08-cloud-deployment.md), and the relevant operations plan/runbook |
| Implement a phase | [Current state](current-state.md), that exact phase plan, and predecessor release/review evidence |
| Historical verification | The specific release or review record needed for the question |

Phase implementation remains subject to the user's current request and the
repository's gates; the presence of a plan alone does not authorize it.

## Document index

| Document | Role |
| --- | --- |
| [Current state](current-state.md) | Factual current implementation, acceptance status, and open gates |
| [Product brief](01-product-brief.md) | Product purpose, user, scope, and principles |
| [Product design](02-product-design.md) | UX direction and application surfaces |
| [Architecture](03-architecture.md) | System boundaries and component topology |
| [Data model](04-data-model.md) | Relational vocabulary and invariants |
| [Technology choices](05-technology-choices.md) | Technology and cloud tradeoffs |
| [AI integration](06-ai-integration.md) | Typed boundary with `personal-ai-system` |
| [Local development](07-local-development.md) | Local setup, checks, and migration guidance |
| [Cloud deployment](08-cloud-deployment.md) | Hosted topology and deployment direction |
| [Phased roadmap](09-implementation-plan.md) | Phase sequencing and intended scope |
| `phase-1`–`phase-9-implementation-plan.md` | Historical implementation intent; read only for scoped implementation |
| `decisions/` | Decisions, historical rationale, and supersession records |
| `releases/` and `reviews/` | What a phase delivered, what was exercised, and remaining limits |
| `runbooks/` | Operational procedures |
| [Service workflow map](../backend/src/personal_travel/services/README.md) | Entry points and invariants for cross-module backend workflows |
| `10-codex-handoff.md`, `implementation-coordinator.md` | Superseded entry points with redirects |
