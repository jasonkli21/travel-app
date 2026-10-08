# Phase 8 independent review — Luna XHigh handoff

Reviewed on 2026-10-05. Repository: `/Users/jasonkli/projects/personal-travel-app`.
Scope: `359fbd3` through `636971e14fd8df11fc3c511b29eca672269f58e3`, inclusive
(diff base `fc96c05`). Includes the accepted plan/ADR, backend, migration,
frontend, existing tests, and Phase 2/3/5/6 integration boundaries.

The architecture is appropriate: one private byte lifecycle, travel-owned SQL,
explicit snapshot downloads, escaped HTML/ICS text, owner authorization,
bounded binary proxying, and independent default-off gates. **The implementation
is not ready to close Phase 8.** Findings below are actionable implementation
defects or unmet local acceptance requirements. GCS, live OAuth, cloud
deployment, full offline synchronization, and Phase 9 are intentionally gated;
their absence is not a defect. No substantive fixes were made during review.

For the fresh **Luna XHigh** session: read `AGENTS.md` and its required documents,
then address these findings within Phase 8, adding meaningful regression tests.
Keep private gates off and use synthetic documents and verified synthetic
identities. Do not provision cloud resources or start Phase 9. Update the release
record only with checks actually completed. P1 means resolve before enabling
the affected private feature; P2 means a correctness/acceptance fix required
before phase closure. Static concurrency findings need deterministic tests;
they are not claims that a live production incident occurred.

## Findings

### F01 — P1: Concurrent upload recovery can replace another writer's temporary file

**Affected:** `backend/src/personal_travel/api/routes/attachments.py:235–263`,
`services/attachments.py::register_upload/mark_ready`, `services/source_store.py`.

A same-key retry seeing `pending` can delete and rewrite the registered object's
`.tmp` while its creator or another retry still owns that path. SQL registration
does not claim byte promotion. Competing promotions can raise uncaught
`FileNotFoundError`/`FileExistsError`; a creator can also link a replacement temp
while the retry is writing it, then mark the object ready without checking its
final digest. The lifecycle's complete-file atomicity no longer holds.

**Expected:** establish one promotion/recovery owner; retries must not unlink an
active writer's temp. Reconcile an existing final object by digest, and handle
concurrent promotion/deletion as explicit lifecycle outcomes. Keep filesystem
work outside SQL locks; a generic workflow framework is unnecessary.

**Validate:** barrier-controlled creator/retry and retry/retry races, including
partial writes, failed writes, promotion failure, deletion, and interruption.
One metadata row and one verified complete object must remain, or a recoverable
unavailable state; never report incomplete bytes ready.

### F02 — P1: Export bounds apply after potentially unbounded work and do not enforce a rendering deadline

**Affected:** `backend/src/personal_travel/services/trip_exports.py:61–139,
268–341`, `repositories/trips.py::get`, `services/conflicts.py`, export route.

The export eagerly loads the entire aggregate and locks all its places before
checking selected counts. It computes every reservation/item conflict before
those checks, under an exclusive trip lock. Conflict storage can grow as
reservations × items, even for a small requested date subset. The 10-second
timer starts only after projection; JSON never checks it, HTML checks only at
day boundaries, and the final document read/compression has no subsequent
deadline check. The 10 MiB limit is checked after materializing the full output.
An oversized trip can consume substantial memory/CPU and block trip access
before returning its intended safe error; a proxy timeout does not stop that work.

**Expected:** bound selected SQL projection and conflict output before expensive
allocation; compute only needed conflicts. Release locks promptly and enforce
actual time/output limits throughout rendering, including JSON and final ZIP
work. Follow the plan's bounded worker/process requirement with the smallest
concrete design; no persisted job system is needed.

**Validate:** oversized aggregate with a small selected scope, densely overlapping
reservations/items, long notes, large JSON, and slow final bundle work. Require
bounded failure, complete accepted artifacts, prompt lock release, and no
successful artifact after the deadline.

### F03 — P2: Date subsets treat start-only reservations as indefinitely running

**Affected:** `backend/src/personal_travel/services/trip_exports.py:111–127`.

When `ends_at` is null, selection has no lower-bound exclusion. A start-only
reservation on October 1 is included in an October 3-only export. Phase 2 treats
a single endpoint as a point event, not an open-ended stay. This was reproduced
against `build_export` with a synthetic projection.

**Expected:** use the domain's point/interval semantics for date overlap in the
trip timezone, including a documented midnight boundary rule.

**Validate:** start-only bookings before/on/after the scope, ordinary multi-day
bookings, midnight endpoints, and timezone/DST cases in every export format.

### F04 — P2: Calendar deduplication can discard the actual booking schedule and private details

**Affected:** `backend/src/personal_travel/services/trip_exports.py::_render_ics,
_ics_item, _ics_reservation` (linked-ID collection around line 448).

Any selected non-cancelled linked item suppresses its reservation event. Links
do not require equivalent schedules. A flexible lodging item linked to a timed
multi-day booking therefore produces only an all-day item; the booking's
check-in/checkout instants disappear. This was reproduced. Even matching linked
items do not inherit reservation confirmation codes/notes when private fields
are requested, so deduplication drops those too.

**Expected:** deduplicate only when the retained event actually represents the
booking, preserving its authoritative schedule and opted-in booking details;
otherwise include the distinct anchor. Keep the explicit include-both option.

**Validate:** flexible/point/different-time linked items, multi-day bookings,
multiple links, cancelled links, date subsets, private opt-in, and include-both.
Check stable UIDs and independently parse the result.

### F05 — P2: HTML/private-field controls promise data the serializers omit

**Affected:** `backend/src/personal_travel/services/trip_exports.py::_render_html,
_ics_reservation`, `frontend/components/trip-workspace/phase-eight-panels.tsx`.

The export checkbox explicitly includes source references, but HTML and ICS
discard `source_reference` even when it is projected with private opt-in. HTML
also never renders `saved_places` or their opted-in notes although the snapshot
declares that section included. A synthetic HTML export confirmed the missing
source reference. This fails P8.2's completeness requirement and makes the
download controls misleading.

**Expected:** preserve the promised supported fields/sections in the readable
snapshot, escaping them safely. State format-specific omissions explicitly
where a format cannot carry a section; do not advertise unsupported inclusion.

**Validate:** unique sentinel values in every opted-in field and saved candidate;
assert inclusion/omission for HTML/ICS/JSON, default privacy, and property/markup
injection resistance. Inspect the resulting printable HTML.

### F06 — P2: Document bundles lose the document-to-reservation relationship

**Affected:** `backend/src/personal_travel/services/trip_exports.py:182–193`,
HTML document list and `travel-trip-export-v1` JSON contract.

Bundled attachment metadata contains only filename, type, size, and archive
path. It drops the stored reservation link and attachment identity. The offline
bundle cannot identify which booking a document belongs to, even though linking
documents to bookings is an authoritative Phase 8 capability. Generic or
similar filenames make this materially less useful during travel. The plan's
export contract does not spell out this relationship preservation.

**Expected:** document a minimal versioned manifest preserving attachment
identity and optional reservation identity, and show the association in HTML.
Exclude owner secrets and object keys; do not add a restore endpoint.

**Validate:** linked/unlinked documents, repeated display names, reservation
unlinking, date scope, and extracted ZIP/JSON relationships without internal
storage references.

### F07 — P2: Travel mode hides live bookings linked only to cancelled items and omits linked booking end times

**Affected:** `frontend/components/trip-travel-mode.tsx:158–201,216–233`.

Itinerary rendering filters cancelled items, but the fallback reservation list
requires `linked_items.length === 0`. A confirmed reservation linked only to
cancelled items is absent from both views. Visible linked anchors show only the
start schedule; arrival/checkout/end details appear only for unlinked bookings.
A link should not make essential authoritative booking data disappear.

**Expected:** derive fallback membership from actually rendered anchors and show
both endpoints for linked bookings, with a clear status and partial schedule.

**Validate:** confirmed booking with only cancelled links, mixed links, multiple
links, flights and multi-day lodging, cancelled reservations, and unscheduled
bookings in a rendered browser view.

### F08 — P2: End-only itinerary points are labelled flexible in HTML and travel mode

**Affected:** `backend/src/personal_travel/services/trip_exports.py:603–605`,
`frontend/components/trip-travel-mode.tsx:175`.

Itinerary services allow an end-only point schedule. Both readable surfaces
render it as `Flexible…–HH:MM`, implying an unscheduled range instead of its
actual point time. ICS already uses that endpoint as DTSTART, so formats disagree.

**Expected:** display a single supplied endpoint as a point time and reserve the
flexible label for two null endpoints; make start-only/range labels consistent.

**Validate:** all four endpoint combinations in browser and static HTML, compared
with the independently parsed ICS schedule.

### F09 — P2: Travel mode can mix revisions and accept obsolete async results

**Affected:** `frontend/components/trip-travel-mode.tsx:42–81` and the existing
trip/reservation/attachment read contracts.

Trip detail is read before separate reservation/document requests. An intervening
edit can combine an old itinerary with newer booking links/conflicts and still
label the screen with the old trip revision. Refreshes have no request-generation
guard; a slower response can replace newer state. A logistics request started
before a refresh can repopulate estimates after refresh cleared them, attaching
old transfer warnings to new itinerary data. Existing read contracts do not
prove a common snapshot for this composition.

**Expected:** use a minimal revision-checked composition or one consistent
projection, and invalidate/discard async results when their trip/place footprint
or relevant request parameters change. No offline cache/sync system is needed.

**Validate:** edits between reads, overlapping refreshes completed in reverse
order, shared-place changes, and a delayed estimate completing after refresh.
No mixed projection or stale warning should be presented as current.

### F10 — P2: Optional attachment failure blocks the entire travel view

**Affected:** `frontend/components/trip-travel-mode.tsx:44–59,88–93`.

`setTrip` occurs only after the attachment request succeeds. If the frontend
attachment gate is on and the backend gate is off, local auth is active, or the
private store is unavailable, a successful ordinary trip read is discarded.
The initial page shows only an error/back link. On later refresh, a document
failure also prevents updating otherwise available itinerary data. This breaks
the application's manual-first graceful-degradation intent.

**Expected:** render successfully loaded core travel data and handle document
unavailability within the document section. Preserve identity failures' existing
sign-in behavior; do not weaken private access gates.

**Validate:** enabled web gate with disabled backend/local-auth mode, failing
private storage, attachment request rejection, retry, and session expiry.

### F11 — P2: Attachment mutations bypass stale/unknown-outcome recovery

**Affected:** `frontend/components/trip-workspace/phase-eight-panels.tsx:91–157`,
`frontend/components/trip-workspace.tsx` integration, existing mutation/revision
recovery helpers.

Upload/patch/delete catch every failure as a message and re-enable controls.
They do not block/reconcile on revision 409 or an uncertain committed write.
An interrupted pending upload or lost PATCH/DELETE response can already advance
the trip revision. PATCH/DELETE retries then use the obsolete revision; same-key
upload replay does correctly bypass that precondition, but the UI provides no
general reconciliation path for the resulting stale workspace. The
parent's reload control is shown only when its own stale state is set, which
these catches do not do. Reselecting a file after a lost upload response resets
its request key; after a reload this can create a second copy of the same upload.

**Expected:** reuse existing stale/unknown-write recovery semantics, expose a
reload/reconcile action, preserve the same upload key for an unresolved attempt,
and distinguish committed-but-refresh-failed from rejected/unknown outcomes.

**Validate:** precondition conflict, pending registration followed by promotion
failure, lost success responses for all mutations, failed refresh, and file
reselection during unresolved recovery. No accidental duplicate or stale retry
loop; ordinary validation errors should remain editable.

### F12 — P2: Refreshed attachment editors retain old values and can overwrite newer metadata

**Affected:** `frontend/components/trip-workspace/phase-eight-panels.tsx:224–235`
and update payload at lines 120–131.

Label/link controls use `defaultValue` under a stable attachment-ID key. After
metadata refresh, React retains the previous input values while the handler
submits both fields with the newly refreshed trip revision. A changed link from
another tab can therefore be overwritten by an old selection without a stale
409; backend filename sanitization also leaves the displayed editor out of sync.

**Expected:** reset/rebase editors explicitly on metadata/recovery changes, or
use controlled drafts tied to the metadata version with a clear dirty-state
policy. Submit intentional edits rather than silently applying stale defaults.

**Validate:** another-tab relabel/relink followed by refresh/save, reservation
deletion/unlink, sanitized labels, and recovery reload while an editor is open.

### F13 — P2: Corrupt ready documents are still advertised as downloadable

**Affected:** `backend/src/personal_travel/api/routes/attachments.py:86–93,
118–132,update_attachment`, attachment listing/service metadata.

Availability checks only file mode/type/size. A same-size corrupted object is
returned as `ready` with `download_available=true`, even though download then
fails its digest check. This was reproduced with a private synthetic object.
The plan explicitly requires corrupt/missing bytes not to appear ready.

**Expected:** base advertised readiness on verified integrity, with bounded
checks or an explicitly validated lifecycle state. Surface unavailable bytes
consistently and preserve recoverable deletion. Never relax download hashing.

**Validate:** same-size digest mismatch, truncated/missing bytes, bad mode/type,
listing/patch/download/bundle consistency, and cleanup.

### F14 — P2: Image validators accept structurally invalid documents

**Affected:** `backend/src/personal_travel/services/attachment_validation.py`
(`_validate_png`, `_validate_jpeg`).

PNG validation accepts valid chunk CRCs with an IDAT payload that is not even a
zlib stream. JPEG validation stops at the first SOS and accepts zero image
components with no scan data. Both examples passed synthetic probes. Width ×
height bounds and signatures alone do not establish the promised valid image
document; these files can be stored as ready despite being unusable downloads.

**Expected:** validate actual supported image structure/data with strict byte,
pixel, decompression, memory and time bounds. If decoding is used, isolate it as
the plan requires. Preserve inert downloads and reject unsupported animation.

**Validate:** valid representative PNG/JPEG, truncated/invalid scan and compressed
data, invalid depth/color/component combinations, multi-frame tricks, animation,
and decompression/pixel limits; malformed uploads must not become ready records.

### F15 — P2: New display/export surfaces drop required persisted place attribution

**Affected:** `frontend/components/trip-travel-mode.tsx` place displays,
`backend/src/personal_travel/services/trip_exports.py::_render_html/_ics_event`.

ADR 0007 explicitly requires source attribution when imported places are shown
away from the map. Travel mode shows provider-derived places without their
stored attribution. HTML adds attribution only to item places, omitting it for
reservation places; ICS carries provider-derived LOCATION without source credit.
The Phase 8 plan also requires credited exported external content. This is a
missed integration requirement, not a request to add remote fetching.

**Expected:** carry and visibly render the relevant persisted credits for every
included provider place, using safe source links where appropriate. Keep manual
places simple and preserve export privacy defaults.

**Validate:** Geoapify and OSM/Nominatim places used only by reservations, by
items, and by saved candidates in browser and exports; manual-place controls;
malicious source-link/attribution text; no network resource retrieval.

### F16 — P2: Required Phase 8 tests are absent and existing regression gates are broken

**Affected:** `backend/tests`, `frontend/tests`, especially
`backend/tests/test_review_migrations.py:71,97–104` and
`frontend/tests/proxy.test.mjs:320–339`; Phase 8 release verification.

None of the reviewed commits adds or updates tests. The frontend suite actually
fails because the shared binary proxy changed `source_response_too_large` to
`binary_response_too_large`. Static review finds two migrated PostgreSQL test
failures: the rollback test expects head `0013`, and a raw source INSERT omits
the new non-null `purpose` after migration 0014 removes its server default.
Existing Phase 6 tests do not cover the new purpose, non-expiring documents,
reservation links, export serializers, or UI recovery.

**Expected:** repair intentional compatibility/test changes without weakening
the underlying assertions. Add meaningful synthetic Phase 8 service/API,
concurrency, proxy, serializer and browser coverage, then run the required
local exit checks. Include migration 0013→0014 with existing booking sources,
ORM parity, upgrade/downgrade behavior, same-owner/trip validation, reservation
SET NULL, trip tombstones/cleanup, interrupted uploads, authenticated reads,
safe headers and bounded binary error/204/redirect/cancellation paths.

**Validate:** complete migrated PostgreSQL suite without skips, frontend suite,
lint/types/build, independent ICS parsing (UTC/DST, all-day ends, UID/SEQUENCE,
Unicode folding and injection), offline HTML/print and ZIP inspection, and
mobile/keyboard document access. Record external OAuth/GCS checks separately
as gated, not silently substituted with mocks.

## Review evidence and limits

- Existing backend suite: **130 passed, 122 skipped**, with four initial failures
  caused by sandbox-denied loopback sockets. The complete Google verification
  test file rerun with loopback access passed **26/26**, including those cases.
- PostgreSQL regression tests could not run: the documented local connection
  refused connections even outside the sandbox, and Docker is unavailable on
  this shell's PATH. No user database was altered. The migration issues in F16
  are static findings, not reported as executed failures.
- Existing frontend tests: **48 passed, 1 failed**, with the exact error-code
  mismatch described in F16.
- Synthetic Python probes exercised the production export projection/renderers,
  media validators and private-store availability functions. They reproduced
  F03, F04, the source-reference part of F05, F13, and F14. Export SQL was replaced
  with a synthetic repository/session for these probes; they do not verify
  PostgreSQL locking or migration behavior.
- No live OAuth/provider/private user data was used. Independent ICS parser,
  visual print/browser review, and cloud storage checks remain unverified.
- Probe script is temporary at `/private/tmp/phase8-review-probes.py`; all durable
  findings and validation requirements are captured here. The review added only
  this handoff document and made no application changes.
