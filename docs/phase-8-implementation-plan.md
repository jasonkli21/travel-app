# Phase 8 implementation plan — attachments, exports and travel mode

**Status:** planned; no Phase 8 implementation delivered
**Date:** 2026-10-03
**Baseline:** reviewed Phase 0–4 commit `56c0cbf`
**Dependencies:** [Phase 5](phase-5-implementation-plan.md) versions and
[Phase 6](phase-6-implementation-plan.md) authentication/private blob lifecycle
**Roadmap:** [phased implementation plan](09-implementation-plan.md)

## Goal and scope boundary

Give the verified traveler reliable access to reservation documents and a
portable itinerary when connectivity is poor. Extend the secure Phase 6
source lifecycle instead of creating a parallel attachment system. Produce
explicitly downloaded snapshots with understandable freshness and privacy.

Deliver trip/reservation attachments, authorized local storage and an optional
accepted GCS adapter, deterministic calendar export, printable PDF/static HTML,
versioned machine-readable owner/trip export and a read-focused travel view.
Phase 7 rich research is not a prerequisite for exporting authoritative state;
include its evidence only when explicit rights and user selection permit.

Defer full offline sync, service-worker caching of private API traffic,
collaboration, public share links, native mobile apps, background location,
push notifications, route optimization and calendar subscription/sync. Download
is not a promise that calendar providers synchronize deletion or later changes.
No bucket, credential or cloud deployment is created without separate
authorization.

## Decisions

| Concern | Decision |
| --- | --- |
| Attachment ownership | Each object belongs to a verified owner and trip; optional reservation link must belong to the same trip/owner. Reuse Phase 6 metadata/lifecycle and extend it with the actual attachment relationship fields. |
| Storage | Local filesystem remains first-class. Add one small concrete local/GCS boundary only when the GCS adapter is authorized; private object keys never become public URLs. Do not add a generic storage/plugin platform. |
| Upload | Maintain authenticated, streamed, per-type size/page/count limits; opaque keys, media sniffing, hash and inert source handling. Begin with already validated PDF/text/image types, adding an image pixel/decompression bound before accepting images. |
| Access | Backend-authorized download by default, no-store and safe Content-Disposition. If GCS signed URLs are needed, short expiry, narrow read scope and no public bucket; treat them as bearer secrets and never log them. |
| Delete/recovery | Reuse pending/ready/deleting metadata and idempotent orphan cleanup. Reservation deletion clears the link but preserves a trip attachment unless the user explicitly deletes it; trip deletion schedules bytes for cleanup before losing references. |
| Export snapshot | Capture authoritative SQL state and its revision/dependency footprint consistently, release locks, then render. A generated artifact includes generation time, trip timezone and revision. |
| Calendar | Stable event UID per item/reservation, sequence tied to authoritative revisions, valid escaping and UTC timed instants. Untimed items use date-only all-day representation, clearly described as flexible day plans. |
| Offline | User explicitly downloads a static snapshot. No background refresh, private cache mirroring or offline edits. Include a freshness warning and instructions to regenerate after changes. |
| External content | Do not fetch document URLs, remote images or map tiles during export. Optional existing licensed geometry/source citations are escaped, credited and marked as observations. |
| Privacy | Exports default to itinerary/place schedule fields; confirmation codes, private notes and documents are opt-in. Never export credentials, internal blob keys or upstream private session records. |

Record attachment retention/delete rules, upload types/bounds, export inclusion
defaults and optional GCS access policy in an ADR. Storage retention must be
compatible with Phase 6 import source deletion rather than silently retaining a
second copy.

## Data and HTTP contracts

Extend attachment metadata only where needed: reservation_id FK with SET NULL,
user display label, validated content disposition/type and retention intent.
Opaque storage keys/hash/size/owner/trip remain server fields. Reuse a single
attachment table/store, not separate import-document vs reservation-document
byte systems. SQL validates relationships through owner/trip services.
Reuse Phase 6's nullable trip FK/deletion tombstones so trip deletion retains
the object key until bytes are removed. Detached tombstones are not downloadable
attachments; cleanup alone can finalize them. Rendering with untrusted image
or document inputs uses an isolated process with enforced time/memory bounds.

Travel-side route targets:

| Method | Route | Behavior |
| --- | --- | --- |
| GET/POST | /v1/trips/{trip_id}/attachments | Owner-scoped metadata listing or bounded authenticated upload; optional same-trip reservation link. |
| PATCH | /v1/trips/{trip_id}/attachments/{attachment_id} | Edit display label or same-trip reservation link under revision/precondition rules. |
| GET | /v1/trips/{trip_id}/attachments/{attachment_id}/download | Reauthorize on every read; bounded private object response, safe filename and no active preview. |
| DELETE | /v1/trips/{trip_id}/attachments/{attachment_id} | Explicit deletion using the existing recoverable lifecycle/cleanup. |
| POST | /v1/trips/{trip_id}/exports | Format, date scope and explicit private-field inclusion; produce a bounded revision-stamped artifact or a safe too-large response. |
| GET | /v1/trips/{trip_id}/travel | Optional read-focused API projection only if existing detail is insufficient; otherwise compose the UI from existing typed data. |

Avoid a persisted export-job table for small request-owned rendering. If measured
PDF/export work cannot fit the request deadline, reduce scope or explicitly
accept a recoverable job design; do not add a queue by habit. Rendering runs in
a bounded worker/process with time/size limits outside database transactions.
The web proxy must support bounded binary responses separately from its JSON
response reader; it cannot buffer unbounded documents in memory.

Export formats:

- ICS for scheduled items/reservations and date-only flexible plans;
- PDF or fully self-contained static HTML for printable/readable travel mode;
- schema-versioned JSON for owner-authorized authoritative trip data.

The JSON export is not an executable database dump or an automatic restore
endpoint. Define its schema and omitted fields; Phase 9 may add a validated
transfer/recovery tool with a separate dry run and ownership policy.

## Calendar and rendering correctness

Use stable namespace-based UID values; changing an item updates the event with
the same UID. Include valid DTSTAMP, SEQUENCE and trip title.
Timed events use stored aware instants rendered as UTC to avoid handcrafted
VTIMEZONE definitions. Date-only events use VALUE=DATE and an exclusive next-day
DTEND. Point/partial schedules follow documented rules; do not invent a duration.
If linked item/reservation events overlap intentionally, offer a deterministic
deduplication choice rather than duplicate booking anchors by default.

Escape backslashes/newlines/commas/semicolons and fold ICS lines by octets.
Untrusted titles/notes must not inject properties or additional events. Test
with an independent parser. Missing/deleted items are omitted from a new
snapshot; explain that importing a file into a calendar is provider-specific
and does not establish a live subscription or guarantee cancellation.

HTML/PDF rendering escapes user fields, disables executable content and remote
resource retrieval, and uses fixed templates with safe source links. Avoid
rendering arbitrary stored markup or passing user fields into shell commands.
Bound paper layout and embedded image dimensions; long notes/Unicode/large
trips cannot truncate authoritative entries silently. Oversize exports produce
an actionable error or explicit selected-date subset.

## Travel view and attachment UX

Add attachment list/upload/link/download/delete with clear pending/missing/
failed/deleting states and source-retention context. Never show a ready link
for missing bytes. Confirm permanent source deletion and explain whether a
reservation keeps metadata/history when its document is removed.

Build a compact mobile-readable day schedule with reservation anchors, timezone,
place/address, conflict/transfer warnings and document download actions.
Connectivity failures preserve the last visible state but do not imply it is
cached offline. Downloads show date scope, private-field inclusion and snapshot
revision; a static copy labels estimates/observations and stale data clearly.

Do not auto-export documents or confirmations just because they are linked.
Offer explicit inclusion before a potentially sensitive file is generated.

## Dependency map and work packages

~~~text
P8.0 attachment/export/rights ADR
           |
P8.1 extend Phase 6 attachment relationships/access/cleanup
           |
P8.2 consistent snapshot + export serializers/renderers
           |
P8.3 bounded binary proxy + attachment/export/travel UX
           |
P8.4 optional authorized GCS adapter + verification/release
~~~

### P8.0 — Accept lifecycle and export semantics

Close type/size/pixel limits, reservation/trip delete behavior, export privacy
defaults, calendar UID/date rules and licensed optional content.

**Acceptance:** one storage lifecycle covers imported and manually attached
documents; no public share/offline-sync behavior is implied.

### P8.1 — Extend attachment services

Add only required fields/migration and owner/trip relationship checks; implement
safe read/upload/delete and reused cleanup.

**Acceptance:** cross-owner/trip references fail before byte access; deletion,
missing objects and interrupted operations are recoverable/idempotent.

### P8.2 — Implement snapshot exports

Project consistent revision-stamped state and write concrete ICS, printable
PDF/static HTML and JSON serializers. Run bounded rendering outside locks.

**Acceptance:** complete data is included according to explicit scope/privacy
options, syntax is independently parsed/rendered, and injection/remote-fetch
attempts fail.

### P8.3 — Deliver binary handling and travel UX

Add bounded upload/download/export proxy behavior, attachment management,
snapshot inclusion controls and read-focused responsive travel view.

**Acceptance:** binary 204/errors/content-disposition/size/cancellation behave
correctly; downloaded files remain usable offline and visibly dated.

### P8.4 — Verify optional storage and record release

Implement GCS only after explicit authorization and accepted IAM/region/access
policy. Test local behavior first; use a private disposable cloud object for
separately authorized smoke tests. Record exact storage/export verification.

**Acceptance:** local mode needs no cloud credential. If GCS is not exercised,
record it as unverified/gated, not delivered by documentation alone.

## Failure and verification matrix

| Area | Required cases |
| --- | --- |
| Attachment SQL/auth | Migration/parity, owner/trip/reservation mismatch, metadata revision conflict, reservation SET NULL, trip byte cleanup, retention alignment with imports |
| Byte lifecycle | Chunked size limits, MIME mismatch, filename/path traversal, image decompression/pixels, interrupted upload, hash mismatch, missing/corrupt bytes, repeated delete/cleanup |
| Access/proxy | Auth expiry/cross-owner download, no cache/public URL, safe headers, bounded binary streams, redirects, disconnect/cancellation, storage timeout |
| ICS | UTC/DST, all-day exclusive end, point/partial schedules, stable UID/SEQUENCE, linked anchors, Unicode/octet folding, escaped property injection, independent parser |
| PDF/HTML/JSON | Long/Unicode notes, multi-page trip, safe links, no scripts/remote fetch, versioned JSON shape, explicit private-field opt-in, snapshot versions and oversize behavior |
| UX/offline | Mobile/keyboard document access, readable print/export, airplane-mode static artifact, generation/freshness labels, saved-but-refresh-failed state and no implied synchronization |
| Optional GCS | Private IAM/bucket, signed URL lifetime if used, key isolation, local/cloud parity, orphan recovery and credential failure |

Run complete migrated backend/API tests, export parser/security regressions,
frontend lint/types/tests/build, package checks and visual PDF/HTML verification.
Record exported fixtures/artifact inspection and authorized cloud results
separately. Do not require live cloud storage for ordinary tests.

## Commit sequence and exit gate

1. docs: accept attachment lifecycle and export semantics.
2. feat: extend private trip/reservation attachment services.
3. feat: add safe revision-stamped itinerary exports.
4. feat: deliver bounded download/upload and travel-mode UX.
5. feat: add authorized optional GCS adapter, if approved.
6. docs: record release and storage/export verification.

Phase 8 completes when private attachment lifecycle and deterministic downloadable
snapshots work locally, supported formats are verified, and offline use is
accurately bounded. GCS stays gated until authorized/exercised. Phase 9 owns
hosted recovery, retention automation and operational monitoring of this
concrete storage system.
