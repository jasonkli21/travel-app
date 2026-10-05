# ADR 0013 — Versioned booking-document extraction and explicit confirmation

- **Status:** accepted for the reviewed local implementation; external private-input gates remain closed
- **Date:** 2026-10-05
- **Scope:** P6.3–P6.5 travel integration

## Context

Booking confirmations contain sensitive personal data and untrusted text. The
travel application must own the original source, candidate corrections, trip
interpretation, reservation writes, and retention choice. The existing
`research-v1` capability is not an extraction contract, and model output must
not directly mutate travel state.

## Decision

Use a separate `booking-document-extraction-v1` HTTP capability with a typed
travel client. The travel client pins upstream source commit
`ebd00a8e2fb2d8b59a5fb5fa3aa44268e5e79b63`. A verified travel owner submits
bounded extracted text only after an explicit action. The upstream receives a
source hash and stable idempotency key, treats the text as data, and returns at
most ten validated candidates with literal source spans and explicit
uncertainty. It has no travel identifiers, memory/search access, tools, link
following, or URL fetching. The travel database retains the pinned upstream
revision with the import and saved confirmation outcome.

Travel keeps durable upload/extraction claims outside SQL-bound network waits.
The original source-byte digest remains distinct from the extracted UTF-8 text
digest required by the upstream contract. An uncertain POST is reconciled with
GET on its original key; a pre-dispatch interruption can safely retry the same
key, and no replacement key is invented. Owner, trip, both digests, and import
identity are checked before saving candidates. Source deletion uses the
existing source lifecycle service and sends delete-by-key so a tombstone can
fence a delayed POST. Migration `0013` keeps minimal owner/key/hash deletion
intents across trip deletion and rejected-result cleanup, independently of the
original-file retention choice.

The owner reviews source excerpts, uncertainty, dates, timezone context and
advisory duplicate matches. Confirmation requires an explicit create/link/skip
choice for every candidate, checks expected trip/import revisions, and commits
the selected batch under the existing reservation rules in one transaction.
The traveler chooses tentative or confirmed for each new reservation; this
status is included in the confirmation fingerprint. Replays return the saved candidate-specific
outcomes without creating another reservation. Source retention defaults to
delete after confirmation or rejection; raw bytes, excerpts and upstream
results have explicit deletion/expiry behavior, while the confirmation outcome
survives source cleanup.

Travel intake/extraction and upstream extraction/provider gates default off.
The local fake generator is restricted to explicit synthetic test fixtures.
The coordinator's final whole-Phase 6 verification and external identity,
provider data-use, service IAM, Firestore TTL, and deployment checks remain
prerequisites to sensitive production use.

## Consequences

This keeps booking data within a dedicated, bounded path and makes each
reservation mutation owner-visible and recoverable. It introduces a versioned
upstream contract plus durable import/confirmation metadata and an explicit
review UI. It does not add mailbox access, background ingestion, general
attachments, or cloud source storage.

See the [upstream contract](../../../personal-ai-system/docs/booking-document-extraction-contract.md)
and the [combined Phase 6 candidate release](../releases/phase-6-booking-imports.md).
