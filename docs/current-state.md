# Current state

Updated: 2026-10-07

This is the single living status snapshot. Read the linked release and review
records for detailed scope, checks, and limitations; do not infer delivery from
a plan.

## Accepted locally

- Phases 0–6 are implemented and locally reviewed. Phase 5 proposal handling
  and Phase 6 verified identity, private-source lifecycle, extraction, and
  reservation confirmation remain default-off where external gates apply.
- Evidence: [Phase 0–4 audit](reviews/phase-0-4-audit.md), [Phase 5 release](releases/phase-5-local-proposals.md) and [lifecycle review](reviews/phase-5-lifecycle-review.md), [Phase 6 release](releases/phase-6-booking-imports.md) and [whole-phase review](reviews/phase-6-whole-review.md).

## Implemented, exit gates open

- **Phase 7:** initial default-off source comparison for food, activities,
  neighborhoods, and day trips. Whole-phase evidence/provider gates remain
  open; dates, prices, hours, availability, accessibility, travel duration,
  and preference retrieval are unsupported. See the [release](releases/phase-7-travel-comparison.md), [review](reviews/phase-7-independent-review.md), and [plan](phase-7-implementation-plan.md).
- **Phase 8:** local private trip attachments, revision-stamped HTML/ICS/JSON
  exports, and a read-focused travel view. Whole-phase verification remains
  open. Private input requires verified Google identity and private storage;
  GCS is not implemented. See the [release](releases/phase-8-attachments-exports.md), [ADR 0015](decisions/0015-phase8-attachments-and-exports.md), and [plan](phase-8-implementation-plan.md).
- **Phase 9:** local operational-hardening slice for provider quotas, hosted
  identity configuration, backup/restore tooling, containers, and CI. The
  phase is not accepted: recovery, deletion, security-scan, performance, and
  deployment checks remain open. See the [release](releases/phase-9-operational-hardening.md), [ADR 0016](decisions/0016-phase9-operational-hardening.md), [plan](phase-9-implementation-plan.md), and [operations runbook](runbooks/phase-9-operations.md).

## External gates and boundaries

- Google OAuth, Cloud Run IAM, hosted deployment, and real private-input
  verification are not provisioned or verified.
- GCS is not implemented or authorized. Private imports and attachments remain
  unavailable in local-auth mode and default off.
- Live AI, Geoapify, and provider approval/quality checks are separate from
  local implementation evidence. No live provider result establishes durable
  travel state.
- Existing database upgrades that require legacy data repair need online
  inspection and a backup before migration.

## Current work boundary

The context-architecture cleanup does not authorize a new Travel feature or
phase. Phase 9 remains the latest unfinished plan; any further feature or
operational work needs an explicit current request and must follow its plan and
open gates. Documentation alone never authorizes cloud deployment.
