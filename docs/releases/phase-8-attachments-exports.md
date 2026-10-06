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
  parsed for integrity without rendering pixels and limited to 40 megapixels.
  Download responses are attachments with `nosniff` and `no-store`.
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

### Independent review remediation — 2026-10-05

- Added a stable per-object filesystem lock around attachment promotion,
  recovery, and deletion. Retries verify final and temporary bytes by digest;
  a deterministic partial-write retry test confirms a competing attempt gets
  an in-progress result and cannot publish the partial file.
- Bounded export SQL selection before loading records, capped reservation/item
  comparisons and projected text, applied point-event and half-open local-day
  date semantics, and carried one deadline through projection and rendering.
  Serialization and bundle reads now run in a spawned worker with CPU and wall
  limits, address/data caps where supported, and chunked size-checked output.
  Added serializer regressions for linked booking details, private fields,
  saved-place omissions, document identity/association, escaping, schedule
  endpoints, and the worker-render path.
- Added a single revision-consistent travel-mode projection, discard guards for
  obsolete refresh and route-estimate responses, graceful document-store
  degradation, and attachment mutation recovery/rebased editor behavior.
- Attachment list, update, travel-mode, download, and bundle paths now verify
  content digests before advertising bytes as available. PNG validation checks
  CRCs and bounded decompressed scanlines in an isolated worker; JPEG validation
  checks supported frame, tables, scan, and entropy-segment structure there.
- Full backend suite: **143 passed, 122 skipped**. The skipped database-backed
  tests require the unavailable PostgreSQL test database. Ruff checks and
  formatter checks passed; mypy passed for 98 source files.
- Frontend tests: **49 passed**. Next route type generation, TypeScript
  `--noEmit`, ESLint, and the Next.js production build passed.
- The review handoff file is retained at
  [`../reviews/phase-8-independent-review.md`](../reviews/phase-8-independent-review.md).

## Exit checks still open

- Migrated PostgreSQL/API regression coverage for ownership, upload recovery,
  reservation unlinking, trip cleanup, and migration `0014` upgrade behavior;
  the local database-backed slice was skipped because `TEST_DATABASE_URL` is
  unavailable.
- Independent ICS parser and export injection/security cases, plus inspection
  of HTML/print layout and downloaded ZIP contents.
- Frontend runtime/browser verification and mobile/keyboard review. Automated
  frontend tests and static checks passed, but rendered browser review remains
  open.
- Authorized Google OAuth, provider, or GCS verification. These are external
  gates and were not exercised.

The implementation is therefore a local Phase 8 delivery, not a claim that the
whole-phase acceptance gate has closed.
