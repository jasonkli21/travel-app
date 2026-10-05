# Phase 6 P6.2 — local private-source lifecycle checkpoint

**Status:** implemented locally; P6.2 remediation is complete and whole-Phase 6 review is pending
**Date:** 2026-10-04
**Scope:** P6.2 storage, upload/read/delete lifecycle, and cleanup

P6.2 adds owner-scoped text/PDF intake behind `PRIVATE_IMPORTS_ENABLED=false`.
Enabling the route requires Google OIDC mode and an absolute private directory
outside the repository; local auth mode is denied. Files use opaque random keys,
a mode-0700 directory, and mode-0600 permissions. The database records owner,
trip, source hash/type/size, sanitized display filename, retention and lifecycle
state. It never stores raw source text.

The upload route streams up to 1 MiB of UTF-8 text or 10 MiB of PDF after
session and CSRF checks. PDFs run in a spawned parser with an eight-second wall
and CPU limit, a 512 MiB memory ceiling (Linux address/data-space limits and a
macOS `libproc` RSS watchdog), a 100-page cap, and a 200,000-character cap.
Encrypted, malformed, mislabeled, empty, and scanned-only PDFs fail safely.
Ordinary JSON remains capped at 64 KiB. The same-origin proxy streams only the
exact upload and source-download routes. Source download also has a 10 MiB
response-byte cap and 30-second end-to-end deadline, including body consumption;
overflow, timeout, and downstream cancellation cancel the upstream reader.

Migration `0010` adds owner/trip-scoped `booking_imports` and
`source_attachments`; `0011` retains replay identity after source deletion.
Per-owner/trip request-key and source-hash SQL uniqueness arbitrate concurrent
requests. Reusing a request key with changed content conflicts. A duplicate hash
under a new key returns 409 with instructions to retry the original key, so no
unpersisted request-key alias can be accepted. Import hash, request key and
fingerprint, media type, and size remain in SQL after source bytes/metadata are
removed. Same-key retries replay that import; a later extraction stage can
extend the same durable outcome. Trip deletion still cascades the import while
setting the source trip reference to null. A downgrade to `0010` is refused if
any detached import exists because restoring a required source reference would
discard the durable outcome contract.

Promotion uses a pending SQL record, atomic no-replace file promotion, and a
conditional ready-state commit. A post-promotion SQL failure leaves a pending
record and promoted bytes for cleanup recovery. Conditional state transitions
prevent a deleting source from being resurrected. Synchronous SQL sessions are
owned by lifecycle methods running in worker threads; filesystem write/fsync,
read, promotion, and deletion work are also off the async route's event loop.
Sources expire after seven days; downloads return 410 at the expiry deadline
even if cleanup has not run. Cleanup caps inspected records and elapsed time,
prints a stable UUID cursor for all source metadata (including healthy rows) and
a sorted entry cursor for orphan files, and preserves import replay/outcome
metadata when it deletes expired or explicitly deleted bytes. Cleanup checks
all owners' metadata before deleting an orphan.
Authorized downloads are hash-verified, `no-store`, and marked `nosniff`;
filenames never become file paths or response headers.

## Local verification

- The full migrated-schema PostgreSQL suite passed **205 tests, zero skipped**
  against the dedicated local PostgreSQL 16 test database on port 55433. The
  19 source-lifecycle tests cover owner/trip isolation, replay and changed-key
  conflicts, concurrent retries, retention, durable hash metadata after delete,
  SQL/file promotion recovery, cleanup cursors, pending-promotion/delete and
  cleanup/delete races, authentication before body receive, slow/chunked and
  disconnected uploads, blocked fsync/SQL with a responsive health route and
  worker-owned sessions, and missing/corrupt-file handling. Migration fixtures
  run Alembic parity checks and assert downgrade refuses to discard detached
  import outcomes.
- Ruff check and format check passed. Mypy passed for **81 backend source
  files**.
- Pinned pnpm 10.17.1 passed frontend lint, typecheck, production build, and
  **36 tests**. Proxy tests cover upload streaming beyond 64 KiB and upload
  bounds, plus source response byte limits, full-body deadlines, and downstream
  cancellation.
- The PDF memory-limit test page-touches allocations in a spawned child process
  on this macOS host and observes the production RSS watchdog terminate the
  over-limit worker. Linux/container process-limit behavior remains unverified;
  a measured Linux kill is still required before making that platform claim.
- The ignored frontend `tsconfig.tsbuildinfo` was restored to its pre-check
  SHA-256 `8d5a79214ba4d89115df650a7a9beafaff7876271d334df75e26f3a4650ae19b`.

This checkpoint used synthetic source bytes, synthetic identity sessions, and
a disposable local PostgreSQL database only. No Google OAuth, upstream
extraction API, Cloud Run IAM, cloud storage, personal inbox, or real private
source was used. Extraction, reservation confirmation, and import UI were out
of scope at this checkpoint; those later stages are now recorded in the
[combined Phase 6 candidate release](phase-6-booking-imports.md). Whole-phase
review remains pending and the feature gates stay off by default.
