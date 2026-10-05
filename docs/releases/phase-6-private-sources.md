# Phase 6 P6.2 — local private-source lifecycle checkpoint

**Status:** implemented locally; independent security review pending
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
exact upload and source-download routes; upload overflow, deadline, and
cancellation stay within that narrow path.

Migration `0010` adds owner/trip-scoped `booking_imports` and
`source_attachments`. Per-owner/trip request-key and source-hash uniqueness
make retries idempotent. Trip deletion cascades the import but sets the source's
trip reference to null, preserving a deletion tombstone. Promotion uses a
pending SQL record, atomic no-replace file promotion, and a ready-state commit.
Compensation and bounded rerunnable cleanup handle pending writes, expiration,
explicit deletion, missing/corrupt files, and old orphans while protecting
every object referenced by another owner's metadata. Sources expire after
seven days. Authorized downloads are hash-verified, `no-store`, and marked
`nosniff`; filenames never become file paths or response headers.

## Local verification

- The full migrated-schema PostgreSQL suite passed **195 tests, zero skipped**
  against the dedicated local PostgreSQL 16 test database on port 55433. The
  P6.2 cases cover owner/trip isolation, same-key and same-hash deduplication,
  concurrent identical uploads, SQL/file promotion compensation, trip-delete
  tombstones, missing-blob cleanup, orphan protection for another owner,
  MIME/size/path/symlink/mode checks, PDF encryption/page/text/network/deadline
  behavior, and worker termination when the memory watchdog reports an
  over-limit process. The fixture applies migrations to a disposable schema
  and runs Alembic parity checks.
- Ruff check and format check passed. Mypy passed for **80 backend source
  files**.
- Pinned pnpm 10.17.1 passed frontend lint, typecheck, **33 tests**, and the
  optimized production build. Proxy tests include uploads larger than 64 KiB,
  the upload byte cap and request-key requirement, and binary source downloads
  with inert headers.

Tests used synthetic source bytes and synthetic identity sessions. The memory
watchdog breach test supplies a synthetic over-limit RSS reading; no
high-memory malicious PDF was run. No Google OAuth, upstream extraction API,
Cloud Run IAM, cloud storage, personal inbox, or real private source was used.
No extraction, reservation confirmation, or review UI is included. The feature
gate remains off by default, and Phase 6 is incomplete until the later stages
and independent review are complete.
