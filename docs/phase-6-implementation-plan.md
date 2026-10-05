# Phase 6 implementation plan — authenticated booking and document import

**Status:** P6.0/P6.1 identity and P6.2 secure-source lifecycle implemented
locally (review pending); extraction, confirmation, and UI remain planned
**Date:** 2026-10-03
**Baseline:** reviewed Phase 0–4 commit `56c0cbf`; Phase 5 version/replay slice required
**Roadmap:** [phased implementation plan](09-implementation-plan.md)
**Prerequisites:** [audit](reviews/phase-0-4-audit.md), [Phase 5](phase-5-implementation-plan.md)

## Goal and scope boundary

P6.2 delivers only authenticated local source intake, storage, scoped reads and
deletion, and cleanup. It does not deliver extraction, confirmed reservations,
or review UI and does not complete Phase 6. Its local checkpoint is in
[`releases/phase-6-private-sources.md`](releases/phase-6-private-sources.md).
The feature gate stays off by default. No accepted upstream extraction or
retention contract exists, and no real private input has been used.

A verified owner can manually submit a booking email/document, review extracted
candidate reservation fields with source context, correct uncertainties and
explicitly create reservations once. Private input must remain private,
bounded and recoverable through extraction/storage failures.

Authentication is the **first implementation gate in this phase**, not work
postponed until Phase 9. Deliver verified single-owner identity and owner
migration, minimal secure local blob storage, manual pasted email/plaintext
and bounded PDF upload, accepted typed extraction client, reviewed candidate
reservations, source references and deterministic replay/deduplication.

Defer Gmail/OAuth mailbox connections, continuous ingestion, HTML link/image
fetching, DOCX/archive imports, OCR/scanned PDF unless separately accepted,
autonomous booking creation, calendar sync, AI proposals to reservations,
multi-user collaboration, cloud deployment and a generic ingestion/job engine.
GCS deployment and general attachment UX are Phase 8/9 work; Phase 6 introduces
only the secure storage lifecycle required for its documents.

## Required decisions and external prerequisites

| Concern | Decision/gate |
| --- | --- |
| Identity | Select one supported OIDC identity provider and vetted verifier in an ADR. Verify signature/issuer/audience/expiry, map stable issuer+subject to server-owned owner identity, and allowlist the personal user. Never trust owner IDs or arbitrary identity headers supplied by the browser. |
| Web session | Use secure HttpOnly sessions, appropriate SameSite/CSRF protection and logout/expiry. The proxy propagates only verified server credentials. API routes all use request-derived identity; service-to-AI credentials are audience-bound and independent of browser identity. |
| Local mode | Existing unauthenticated local CRUD may remain behind explicit local mode. Private import/upload routes remain disabled there; tests use a verified identity override and synthetic inputs. No silent fallback to local when token verification fails. |
| Existing owner data | Provide an explicit backed-up local-to-verified-owner migration, with dry run, counts and collision detection. Never auto-claim all local records on first login or derive owners from mutable email addresses. |
| Extraction contract | Accept/pin a separate versioned personal-ai-system extraction contract, including privacy/retention, bounded input/output, idempotency and durable outcome recovery. Phase 4 research-v1 is not an extraction API. |
| Input | Pasted email/plaintext first, then text-bearing PDF. Initial limits: 1 MiB text, 10 MiB PDF, 100 pages and 200,000 extracted characters; finalize against measured parsing and accepted downstream limits. Reject encrypted/unsupported/scanned-only documents clearly. |
| Output | At most ten typed reservation candidates. Missing or uncertain dates/timezones/provider/confirmation fields remain explicit uncertainties, not guessed facts. Confirmation applies a selected corrected batch atomically. |
| Storage | Opaque application-generated object keys, outside source/web roots, restrictive local permissions, byte/hash/MIME validation and short-lived authorized reads. Original filenames are display metadata only. P6.2 uses local storage only and stays off by default. |
| Work lifecycle | Request-driven durable import record with short claim transitions and a bounded extraction deadline. No SQL locks span parsing/provider waits. No worker/queue until measured execution cannot fit the bounded request model. |
| Retention | Input, extraction references and review data have explicit retention/deletion controls. Keep approved originals only with user consent; rejected/abandoned temporary inputs are cleaned up. Do not retain private raw text in logs. |

PDF parsing runs in an isolated process with enforced elapsed-time and memory
limits, no remote-resource access, and termination on limit breach. Page/text
limits alone do not stop decompression or pathological-parser resource use.
Record tested local/container enforcement in the parser ADR before enablement.

Before private input is enabled, verify the upstream authenticated boundary and
its data-use/retention rules with synthetic data. If extraction lacks replay/
detail recovery or exceeds the bounded request budget, stop enablement and
revise the lifecycle ADR rather than adding an unplanned background worker.

## Domain and HTTP contracts

Travel-side route targets:

| Method | Route | Behavior |
| --- | --- | --- |
| POST | /v1/trips/{trip_id}/imports | Authenticated owner; bounded plaintext or PDF submission with request idempotency key. Record immutable source/hash and return import ID/state. |
| POST | /v1/trips/{trip_id}/imports/{import_id}/extract | Explicitly claim/run accepted extraction using the stored downstream key; bounded deadline and safe result/state. |
| GET | /v1/trips/{trip_id}/imports/{import_id} | Owner-scoped source metadata, typed candidates/uncertainties, state and recoverable failure guidance; no raw provider internals. |
| POST | /v1/trips/{trip_id}/imports/{import_id}/confirm | Corrected selected candidates, expected trip/import versions and confirmation key; validate and atomically create/link reservations once. |
| POST | /v1/trips/{trip_id}/imports/{import_id}/reject | Idempotently reject unconfirmed work and schedule retention cleanup. |
| GET/DELETE | /v1/trips/{trip_id}/imports/{import_id}/source | Authenticated source download or explicit source deletion, with reference/lifecycle rules. |

Separate ordinary 64 KiB JSON limits from upload limits: add a narrowly scoped,
streamed upload path after authentication, with content-type/byte/count/time
bounds before buffering. Do not raise the global API/proxy body limit to
accommodate PDFs. The web proxy must stream uploads safely with bounded totals
and cancellation, and must not accept arbitrary upstream URLs.

Candidate values include reservation type/status suggestion, provider label,
confirmation reference, source-local schedule with timezone/offset when known,
optional place suggestion and field-level uncertainty/source spans. AI output
is untrusted extraction. User confirmation supplies validated Phase 2 fields;
unknown place matches require explicit existing-place selection or reviewed
manual creation, not automatic provider import.

Flight documents may use different departure/arrival zones. With known zones/
offsets, deterministically convert each endpoint to the trip's displayed local
schedule and show both original and trip-local values in review. With missing
zones, require explicit user resolution. Preserve Phase 2 cross-midnight/DST
rules and do not invent zones or silently reorder endpoints.

Raw documents may contain instructions. The extraction contract treats them
as data, cannot authorize tools/actions, cannot choose owner/trip IDs, cannot
follow remote links, and cannot include executable content in the UI.

## Persistence, deduplication and recovery

Add migrations for an owner-scoped import record and the minimum attachment
metadata needed by the private source:

- import UUID, owner/trip FK, request key/fingerprint, immutable input SHA-256,
  media type, source attachment/reference, parser/capability versions;
- received/extracting/review_ready/applied/rejected/failed state, bounded claim
  expiry and stored downstream idempotency key;
- validated bounded candidate snapshot, uncertainties, extraction source
  reference/expiry, safe failure code and review revision;
- applied reservation IDs/result and confirmation fingerprint;
- attachment UUID, owner/trip, opaque key, media type, size/hash, sanitized
  display filename, pending/ready/deleting state and retention timestamps.

Keep attachment metadata as a deletion tombstone when its trip is deleted:
the nullable trip FK uses SET NULL, retains owner/key/hash, and detached rows
are inaccessible to normal trip download routes and eligible for cleanup.
Do not cascade-delete the last blob reference. Final metadata removal follows
confirmed byte deletion; imported source links may cascade with their import.

Use SQL uniqueness for owner/trip/request key and confirmed import outcome.
Identical keys with different input/confirmation content conflict. The same
source hash within a trip returns the existing import/review instead of making
duplicates; allow explicit new-source correction only through a documented
workflow. A document may contain multiple candidates: confirmation applies one
selected batch, records skipped candidates, and terminates that import. Later
additions require explicit new review, not replay of already applied candidates.

Cross-document booking duplicates are advisory matches using normalized
provider/reference/schedule. Confirmation offers “link existing” or explicitly
“create separate”; never merge uncertain matches or overwrite manual edits.
The confirmed source-candidate identity cannot produce two reservations.

SQL and object bytes do not share a transaction. Write a bounded temporary
object, hash/validate it, atomically promote it, then mark metadata ready.
Compensate failed SQL writes; a bounded cleanup command reconciles abandoned
temporary objects/metadata and is safe to rerun. Deletion marks state, deletes
bytes, then finalizes metadata. Missing objects never masquerade as ready.
Coordinate trip deletion with orphan cleanup rather than FK-dropping metadata
and losing the only deletion reference. Keep this concrete; no generic workflow
engine or transactional outbox is required for the first local implementation.

Extraction commits a short claim and releases SQL before work. An interrupted
request leaves recoverable state: reconcile against accepted upstream detail/
same-key replay before retrying. Claim expiry alone does not prove remote work
stopped. Confirmation locks trip/import in a fixed order, checks revisions,
validates the selected batch through reservation services, and commits
reservations/source links/import outcome/revision together.

## Frontend behavior

Add an authenticated manual-import screen under a trip. Explain precisely
which source content crosses the configured AI boundary and request explicit
submission. Display filename/type/size, safe text source spans, uncertainties,
original timezone context and editable candidate fields.

Review never creates a booking. Confirmation shows tentative/confirmed status,
deduplication choices and source-retention choice separately. Block unresolved
required fields, unsupported timezone interpretation, expired review or stale
trip/import versions. On an unknown confirm outcome, read import detail before
retrying. A successful confirm followed by refresh failure follows the Phase
0–4 saved-but-stale rule. Source previews are inert; no HTML execution, remote
images or document-supplied links are fetched automatically.

## Dependency map and work packages

~~~text
P6.0 identity/storage/extraction ADRs and upstream acceptance
           |
P6.1 verified identity + owner migration + all-route authorization
           |
P6.2 bounded secure source lifecycle
           |
P6.3 typed extraction + recoverable import lifecycle
           |
P6.4 deterministic reviewed confirmation/deduplication
           |
P6.5 UI + release/security verification
~~~

### P6.0 — Accept the sensitive-data design

ADR 0011 accepts Google OIDC, the server-side browser session, verified stable
owner mapping and independent AI service/user credentials for the identity
boundary. No upstream booking/document extraction or retention contract is
accepted; source types, limits, and recovery semantics remain open for a later
stage.

**Status:** identity decision accepted locally; private imports stay disabled
until a separate HTTP/authentication/retention decision is accepted.

### P6.1 — Implement identity and migrate local ownership

Introduce a small request-owner dependency, verified API/web sessions and
single-user allowlist. Update all existing routes, including research/provider
operations, to derive ownership from it. Add explicit owner migration tooling.
Read-only `/health` and `/ready` probes may remain identity-independent with
the local/network host boundary and safe status-only output; they expose no
owner records. Upload authentication must run before body parsing/byte access.

**Acceptance:** cross-owner and missing/expired/forged credentials fail before
SQL/provider/blob access. Dry-run migration detects collisions and preserves
FKs, revisions, provider-place uniqueness and counts.

**Status:** implemented locally; coordinator review and final verification are
pending. Current evidence is in the identity release checkpoint.

### P6.2 — Build the minimal private blob lifecycle

Implement local opaque-key storage, scoped upload/read/delete routes, parser
limits and reconciliation cleanup. Add SQL metadata/migrations.

**Acceptance:** path traversal, deceptive MIME, oversized streams/PDF page
counts, parser failure, interrupted promotion and missing blobs fail safely;
cleanup is idempotent and cannot delete another owner's objects.

**Status:** implemented locally, review pending. The exact upload route streams
after authentication with separate 1 MiB text/10 MiB PDF and 30-second limits;
ordinary JSON remains capped at 64 KiB. PDFs run in a spawned process with an
8-second wall/CPU cap, a 512 MiB RSS watchdog on macOS or address/data-space
limits on Linux, a 100-page/200,000-character cap, and no remote resource
access. The gate defaults off and local mode is denied. No extraction,
confirmation, UI, cloud storage, or real private input is included.

### P6.3 — Integrate typed extraction

Implement accepted bounded HTTP client, durable claims and result correlation/
recovery. Store only validated review candidates and necessary source references.

**Acceptance:** unavailable/malformed/uncertain extraction never creates a
reservation; replay returns one correlated import; no locks span external work.

### P6.4 — Confirm through reservation services

Add corrected selected-batch validation, timezone conversion, dedupe choices,
same-trip place/link checks, one-transaction outcome and replay.

**Acceptance:** a late invalid candidate rolls back the whole batch; concurrent
confirm, response loss and key reuse cannot duplicate or overwrite reservations.

### P6.5 — Deliver review UX and release evidence

Add upload/paste, uncertainty/source review, confirm/reject/recovery, retention
and authenticated source access UI. Update data/AI/auth/storage/local docs and
release evidence with synthetic vs real-input verification distinctions.

**Acceptance:** a verified owner can review and confirm a synthetic booking,
reopen it with safe source access, and delete/reject input according to policy.

## Failure and verification matrix

| Area | Required cases |
| --- | --- |
| Auth | All existing/new routes, forged issuer/audience/signature, expiry/logout, missing credentials, cross-owner IDs, CSRF, trusted proxy/service identity, no local fallback |
| Migration | Existing local graph/versions, dry-run counts, conflicting provider IDs/owners, backup/rollback limits, clean upgrade/downgrade/parity |
| Input/storage | Chunked oversize, MIME mismatch, path/filename injection, malicious PDF, encrypted/scanned-only source, page/text bounds, interrupted upload, promotion/SQL failure, orphan/missing blob, delete replay |
| Extraction | Accepted success/multiple candidates, prompt injection as data, wrong import/session, uncertain dates/offsets, timeout, malformed/oversized output, upstream unknown outcome and same-key recovery |
| Confirm | Status/place/link rules, cross-midnight/DST/cross-zone schedules, duplicate source/bookings, correction fingerprint mismatch, stale trip/import, concurrent/replayed apply, full rollback |
| UX/privacy | Clear submission disclosure, inert source preview, keyboard/mobile review, unresolved fields, authenticated downloads, saved-but-refresh-failed recovery, logs/retention delete behavior |

Run migrated PostgreSQL/API/concurrency suites, security/parser fixtures,
frontend lint/types/tests/build and package checks. Use synthetic booking data
and mocked accepted extraction by default. Live identity/service boundary tests
are required before sensitive enablement. No personal inbox or cloud resource
is connected merely to verify the phase.

## Commit sequence and exit gate

1. docs: accept identity/import/storage/extraction ADRs and contract.
2. feat: add verified ownership and explicit local owner migration.
3. feat: add secure bounded manual-source storage and lifecycle.
4. feat: extract typed candidates and confirm reservations atomically.
5. feat: deliver authenticated review/recovery UI.
6. docs: record release, security checks and remaining external gates.

Phase 6 completes only after authenticated ownership, bounded private source
handling, deterministic review/confirmation and replay/recovery are proven.
Gmail automation remains deferred even after completion. Phase 8 reuses this
storage/access lifecycle; it does not create a second blob/auth system.
