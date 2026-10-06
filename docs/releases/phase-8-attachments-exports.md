# Phase 8 — Attachments, exports, and travel mode

**Status:** local implementation delivered; whole-phase exit verification open
**Date:** 2026-10-05
**Migration:** `0014_trip_attachments`
**Accepted semantics:** [ADR 0015](../decisions/0015-phase8-attachments-and-exports.md)

## Delivered locally

- Reused `source_attachments` and the opaque local private store for trip
  documents. Migration `0014` adds the purpose, optional reservation link,
  nullable expiry, and upload idempotency fields. Booking sources retain their
  existing expiry behavior; trip documents do not expire automatically.
- Added owner/trip-scoped list, streamed upload, metadata update, safe download,
  and recoverable delete routes. Reservation links are checked against the
  same owner and trip. Reservation deletion clears a link; trip deletion keeps
  a detached cleanup tombstone until bytes can be removed.
- Accepted UTF-8 text up to 1 MiB and PDF/JPEG/PNG up to 10 MiB. PDFs are
  structurally checked in the isolated parser with a 100-page cap. Images are
  structurally checked without decoding and limited to 40 megapixels. Download
  responses are attachments with `nosniff` and `no-store`.
- Added request-owned, revision-stamped HTML, ICS, and
  `travel-trip-export-v1` JSON. Projection locks the trip root and referenced
  places in deterministic order, releases SQL locks before rendering, and
  reports the trip revision and generation time. HTML is self-contained and
  escaped; ICS uses stable UIDs, trip revision sequences, UTC timed events,
  date-only flexible days, escaping, and UTF-8 octet folding.
- Private reservation fields and ready trip documents are opt-in. Selecting
  documents creates a bounded ZIP containing the selected snapshot and files.
  Internal blob keys, credentials, and upstream session records are omitted.
- Added the attachment manager, explicit export controls, and read-focused
  travel page with reservation anchors, conflict indicators, on-demand route
  estimates, and document downloads. The page keeps visible state after a
  refresh failure but does not claim offline cache or synchronization.

## Gates and limits

`PRIVATE_ATTACHMENTS_ENABLED` and the frontend
`NEXT_PUBLIC_PRIVATE_ATTACHMENTS_ENABLED` both default to `false`. Private
attachments require verified Google OIDC and an absolute private source
directory outside the repository. Google OAuth is not provisioned in this
environment, and local-auth mode cannot access private attachments. No real
private files were processed. GCS was not implemented: creating a bucket or
credentials requires separate authorization. Calendar downloads are static
files, not subscriptions or synchronization; offline use requires a user to
download a snapshot explicitly.

## Verification performed

- Backend Ruff check and formatter check passed.
- Backend mypy passed for 96 source files.
- Backend Python bytecode compilation passed.
- Frontend ESLint passed.
- Next route type generation and TypeScript `--noEmit` passed.
- Next.js production build passed, including `/trips/[tripId]/travel`.
- `git diff --check` passed.

## Exit checks still open

- Migrated PostgreSQL/API regression coverage for ownership, upload recovery,
  reservation unlinking, trip cleanup, and migration `0014` upgrade behavior.
- Independent ICS parser and export injection/security cases, plus inspection
  of HTML/print layout and downloaded ZIP contents.
- Frontend runtime/browser verification and mobile/keyboard review.
- Authorized Google OAuth, provider, or GCS verification. These are external
  gates and were not exercised.

The implementation is therefore a local Phase 8 delivery, not a claim that the
whole-phase acceptance gate has closed.
