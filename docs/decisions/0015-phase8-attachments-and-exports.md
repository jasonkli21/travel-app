# ADR 0015 — Phase 8 attachments, static exports, and travel mode

**Status:** accepted for the local Phase 8 implementation; GCS remains gated
**Date:** 2026-10-05
**Scope:** trip/reservation documents and explicit downloadable snapshots

## Context

Phase 6 already provides verified-owner source records, opaque local object
keys, streamed writes, hash checks, safe downloads, deletion tombstones, and a
bounded cleanup command. General trip documents need the same private byte
boundary, but should not inherit booking-source expiry or create a second blob
store. Exports must be usable without a network connection while making their
age, scope, and privacy choices clear.

## Decisions

Use the existing `source_attachments` table and local opaque object store for
both booking sources and trip attachments. Add a purpose marker, optional
same-trip reservation relationship, and optional expiry. Booking sources keep
their existing seven-day expiry; trip attachments have no automatic expiry.
Reservation deletion sets the document relationship to null and preserves the
trip document. Trip deletion marks all related objects deleting in the same
transaction before the trip reference is detached; the existing bounded
cleanup command removes their bytes and metadata after interruption.

Trip attachments require a verified Google owner and a private absolute local
storage directory. The default-off gate is independent of booking extraction.
Accept UTF-8 text up to 1 MiB and PDF, JPEG, and PNG up to 10 MiB. PDFs are
structure-checked in the existing isolated parser process with a 100-page
limit. Text is UTF-8 checked. Images are signature/structure checked and
limited to 40 million pixels; the application never decodes or renders them.
All documents download as attachments with `nosniff` and `no-store`; the UI
does not inline document or image bytes. Filenames remain sanitized display
metadata and never form object paths.

Attachment creation, relinking, relabeling, and deletion require the current
trip revision. The optional reservation reference is validated against the
same verified owner and trip before reading upload bytes. Every download
reauthorizes owner, trip, ready state, expiry, byte count, and digest.

Generate ICS, self-contained static HTML, and versioned JSON snapshots from a
trip-root-locked SQL projection. Capture trip and referenced-place revisions,
then release database locks before rendering or reading selected documents.
The output records generation time, timezone, trip revision, date scope, and
private-field choice. Confirmation codes, notes, and document bytes require
explicit opt-in. Selecting documents returns a ZIP containing the selected
snapshot and the current trip attachments; internal storage keys, credentials,
and upstream session records are never exported. Downloads are static files,
not live subscriptions or offline synchronization.

ICS uses stable record-based UIDs, the captured trip revision as `SEQUENCE`,
UTC instants for timed records, and date-only all-day events for untimed
itinerary items. All-day `DTEND` is exclusive. Open-ended timed records omit
`DTEND`; no duration is invented. Linked reservations are omitted as separate
events by default when an itinerary event already represents them, with an
explicit option to include both. Property text is escaped and lines are folded
by UTF-8 octets. Canceled records are omitted from calendar output.

GCS is not implemented or enabled by this decision. An adapter, bucket,
credentials, IAM binding, or cloud smoke test requires its separate P8.4
authorization and accepted region/access policy.

## Consequences

- Booking-import retention and trip-document retention share bytes and cleanup
  code while remaining distinguishable in SQL.
- Attachments are private and owner-authorized; there are no public URLs or
  signed URLs.
- Static exports can contain sensitive values or documents only after an
  explicit choice, and they can become stale immediately after generation.
- The local attachment/export implementation does not establish cloud or
  production readiness.
