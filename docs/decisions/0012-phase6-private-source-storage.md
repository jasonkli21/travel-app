# ADR 0012 — P6.2 local private-source storage and ingress

**Status:** accepted for the local P6.2 implementation; whole-Phase 6 review pending
**Date:** 2026-10-04
**Scope:** private source intake, storage, authorized access, and cleanup only

## Context

P6.1 establishes a verified owner before private input. P6.2 needs a durable
source lifecycle that can survive request retries, trip deletion, partial SQL
or filesystem failure, and bounded parser failure. No accepted
`personal-ai-system` extraction API or data-retention contract exists. P6.2
therefore stores source bytes locally and does not send them to an AI provider.
The source feature remains disabled by default, and no real private input is
used in local verification.

## Decisions

The `PRIVATE_IMPORTS_ENABLED` setting defaults to false. Enabling it requires
Google OIDC mode and a configured absolute private source directory outside the
application tree. Local mode cannot upload, read, or delete sources. All source
routes require a request-derived verified owner; owner and trip scope are
checked for metadata, bytes, deduplication, and deletion.

The storage directory is mode `0700` and owned by the service user. Each
application-generated 128-bit random key names a regular file with mode `0600`;
user filenames are sanitized display metadata only. File operations use a
pinned directory descriptor, no-follow opens, create-exclusive temporary
files, and no-replace atomic promotion. No browser-served root or repository
path can be configured as the source store. SQL stores the opaque key, media
type, size, SHA-256, sanitized display name, state, and retention time; raw
source bytes and extracted text remain outside SQL.

P6.2 accepts only UTF-8 plain text up to 1 MiB and PDF up to 10 MiB. It caps
text at 200,000 characters and PDF documents at 100 pages, rejects encrypted,
malformed, mislabeled, empty, and scanned-only PDFs, and checks the PDF magic
bytes and hash. The parser runs in a spawned process with an eight-second wall
deadline and CPU limit. On Linux it applies address-space and data limits; on
macOS, where the shared-cache address layout prevents lowering `RLIMIT_AS`, the
parent samples worker RSS using `libproc` and terminates the worker above 512
MiB. If the macOS memory monitor cannot read the child, parsing fails closed.
The worker disables socket and DNS access. Page/text bounds do not replace the
process limits.

The exact authenticated upload route alone bypasses the ordinary body reader.
The API and same-origin proxy stream it with per-media byte caps, a 30-second
receive deadline, and cancellation on timeout or overflow. Authentication,
CSRF, local-mode, and feature-gate checks run before the body is read. Ordinary
JSON remains capped at 64 KiB. No proxy header accepts an arbitrary upstream
URL, and the proxy streams only the exact import upload and source-download
routes.

Each upload has an owner/trip request key and source SHA-256. Reusing a request
key with different content conflicts. A same-trip hash submitted under a new
key returns 409 with instructions to retry using the original key; SQL uniqueness
arbitrates concurrent retries, and no unpersisted key alias is accepted. A
same-key retry replays its import metadata even after source deletion. The SQL
import row is committed in `received` state with a pending source before atomic
blob promotion. A second short SQL transaction conditionally marks the source
ready only if it remains pending and unexpired. A post-promotion SQL failure
leaves a recoverable pending row and bytes; bounded cleanup can promote a
verified object or remove incomplete/expired bytes while preserving the import.
Parsing, filesystem work, and provider work do not run under SQL locks. Synchronous
SQL and filesystem work initiated by async upload handling runs in worker
threads, each lifecycle call owning and closing its own short-lived Session.

An import belongs to its trip and cascades when that trip is deleted. The
source's nullable trip foreign key uses `ON DELETE SET NULL`. Migration `0011`
also makes the import's source foreign key nullable with `ON DELETE SET NULL`,
and retains source hash, media type, size, request key, and request fingerprint
on the import after source bytes and metadata are deleted. Sources expire after
seven days; metadata reports `expired`, while byte downloads return 410 at the
expiry deadline even before cleanup runs. Authorized downloads verify the
stored hash and return an attachment with `nosniff` and `no-store`. Explicit
deletion marks the source deleting before removing bytes; cleanup finalizes
source metadata but does not erase import replay/outcome data. Conditional state
transitions serialize promotion and deletion without holding SQL locks during
file operations. Missing or corrupt bytes fail closed and never appear ready.
Operator cleanup caps inspected records and elapsed time, prints stable cursors
for all source metadata and sorted orphan-file scans, and checks every owner's
metadata before deleting an orphan.

Request logs contain only request IDs, methods, route templates, status,
duration, and exception types. They never contain source bytes, parsed text,
filenames, idempotency keys, or parser traces. No upload/download route writes
raw document content into application logs.

## Consequences and limits

- This is a local filesystem lifecycle, not cloud object storage or a general
  attachment service. The storage directory must be provisioned separately.
- The upload gate remains off by default. Live Google identity and hosted
  private-input use remain external gates.
- P6.2 does not call an extraction provider, create reservation candidates,
  confirm bookings, or add a review UI. Those are later Phase 6 stages and
  require their own accepted extraction/retention contract.
- Parser and lifecycle tests use synthetic inputs and a disposable local
  PostgreSQL database only. The memory-limit test page-touches allocations in
  an isolated child process on macOS and observes the production RSS watchdog;
  Linux process-limit behavior was not run in this verification.
