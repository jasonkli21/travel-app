# Service workflow map

Services own multi-step travel workflows and their transaction boundaries.
Routes validate HTTP input and call services; repositories own SQL queries;
external AI/provider work goes through typed clients. Start with this map when
a change crosses more than one service or persistence boundary.

## High-context workflows

| Workflow | Main entry points | State and invariants | Focused evidence |
| --- | --- | --- | --- |
| Itinerary ordering and revisions | [`itinerary.py`](itinerary.py), [`revisions.py`](revisions.py), [`trips.py`](trips.py), [`../api/routes/itinerary.py`](../api/routes/itinerary.py) | Trip-root transactions serialize order changes; day order is contiguous; expected revisions reject stale writes. | [`test_phase1_postgres.py`](../../../tests/test_phase1_postgres.py), [`test_phase5_revision_postgres.py`](../../../tests/test_phase5_revision_postgres.py), [ADR 0009](../../../../docs/decisions/0009-local-boundaries-and-integrity.md) |
| AI itinerary proposals | [`proposals.py`](proposals.py), [`proposal_preview.py`](proposal_preview.py), [`../domain/proposals.py`](../domain/proposals.py), [`../clients/personal_ai.py`](../clients/personal_ai.py), [`../api/routes/proposals.py`](../api/routes/proposals.py) | Durable proposals move through generation/recovery to ready, failed, applied, or rejected. Travel computes the preview and owns atomic apply/replay; output never writes state without explicit confirmation. | [`test_phase5_proposal_lifecycle.py`](../../../tests/test_phase5_proposal_lifecycle.py), [ADR 0010](../../../../docs/decisions/0010-phase5-proposal-safety.md), [Phase 5 release](../../../../docs/releases/phase-5-local-proposals.md) |
| Private sources and booking imports | [`source_lifecycle.py`](source_lifecycle.py), [`source_store.py`](source_store.py), [`source_cleanup.py`](source_cleanup.py), [`booking_imports.py`](booking_imports.py), [`booking_confirmation.py`](booking_confirmation.py), [`private_deletion.py`](private_deletion.py), [`../api/routes/imports.py`](../api/routes/imports.py) | Source bytes use opaque keys and `pending → ready → deleting` lifecycle; deletion is recoverable. Extraction is bounded and owner/key scoped. Candidate confirmation is explicit, revision checked, atomic, and replayable. | [`test_secure_sources.py`](../../../tests/test_secure_sources.py), [`test_booking_imports.py`](../../../tests/test_booking_imports.py), [ADR 0011](../../../../docs/decisions/0011-phase6-google-identity-and-ai-auth.md), [ADR 0012](../../../../docs/decisions/0012-phase6-private-source-storage.md), [ADR 0013](../../../../docs/decisions/0013-phase6-booking-document-import.md), [Phase 6 release](../../../../docs/releases/phase-6-booking-imports.md) |
| Trip attachments and exports | [`attachments.py`](attachments.py), [`attachment_validation.py`](attachment_validation.py), [`trip_exports.py`](trip_exports.py), [`../api/routes/attachments.py`](../api/routes/attachments.py), [`../api/routes/exports.py`](../api/routes/exports.py) | Attachments reuse the private source lifecycle and same-trip reservation checks. Exports are revision-stamped snapshots; private fields/documents are opt-in; rendering occurs outside SQL locks with resource bounds. | [`test_phase8_review_regressions.py`](../../../tests/test_phase8_review_regressions.py), [ADR 0015](../../../../docs/decisions/0015-phase8-attachments-and-exports.md), [Phase 8 release](../../../../docs/releases/phase-8-attachments-exports.md) |
| Provider admission and comparisons | [`provider_admission.py`](provider_admission.py), [`travel_comparisons.py`](travel_comparisons.py), [`location.py`](location.py), [`../api/middleware.py`](../api/middleware.py) | Provider calls are gated and budgeted before outbound work. Comparison evidence is transient; saving a candidate is a separate validated action. | [`test_provider_admission.py`](../../../tests/test_provider_admission.py), [ADR 0014](../../../../docs/decisions/0014-phase7-travel-comparison.md), [Phase 9 release](../../../../docs/releases/phase-9-operational-hardening.md) |

## Change guidance

- Keep network waits outside SQL transactions and release aggregate locks
  before provider calls; revalidate revisions and ownership before persisting
  returned evidence.
- Keep private bytes out of SQL and public application paths. Require a
  verified request principal for private operations; default-off gates do not
  substitute for authorization.
- Add a migration for persistent schema changes and focused regression
  coverage for lifecycle, replay, concurrency, expiry, or deletion changes.
- Read [current state](../../../../docs/current-state.md) before phase work.
  The relevant plan and release evidence are linked from the
  [documentation router](../../../../docs/README.md).
