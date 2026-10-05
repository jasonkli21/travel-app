# Phase 6 independent whole-phase review

Reviewed in the coordinator session, 2026-10-05. Travel candidate `d596b1a` /
`c036a5b`, source remediation `cf6696b`, identity through `6f0da88`; upstream
candidate `ece8cfc`. The written Phase 6 plan and broader domain/privacy/recovery
intent were checked. Implementation is not yet accepted. Fix findings below
in a fresh Luna Extra High agent, then coordinator verifies and closes locally.

## Findings

1. **High: PDF extraction hash mismatch.** Travel `_read_source` returns the
   original file SHA-256 and submits that as `source_sha256`; upstream request
   validation hashes `document_text`. Every ordinary text-bearing PDF fails
   upstream validation (reproduced with the real request DTO). Maintain distinct
   immutable original-file and extracted-text hashes in the durable protocol,
   verify both relevant identities on recovery/delete, and test actual PDF
   upload → real upstream contract → review → confirmation. Avoid mocks that
   accept an impossible production payload.
2. **High: live deletion credentials are absent.** Travel middleware/proxy only
   recognize `/imports/.../extract` as an AI operation. Confirm/reject/source
   DELETE invoke upstream deletion but have no verified AI user assertion/auth
   context. Under Google/Cloud Run mode these calls fail rather than delivering
   privacy cleanup. Propagate server-only user credentials and independent
   service IAM on the narrowly required deletion/confirmation/rejection routes;
   test realistic authenticated HTTP boundaries, not only local fake clients.
3. **High: durable recovery wedges before POST dispatch.** Travel commits
   `extraction_post_attempted` before dispatch. Interruption in that gap, or a
   deterministic pre-acceptance 4xx, becomes an endless `extracting` state:
   GET by key is 404 and no same-key POST may ever be sent again. Implement a
   bounded same-key recovery protocol using the upstream atomic fence, retaining
   original request identity and no new key; or a clear recoverable terminal
   outcome for conclusively rejected requests. Distinguish malformed/rejected
   responses from uncertain acceptance. Test crash-before-send, response loss,
   missing key, concurrent recovery, deleted tombstone, and expired key.
4. **High: privacy deletion intent is lost or incomplete.** Rejection with
   keep-until-expiry clears local candidates but never deletes the upstream
   result despite the UI promising to discard candidate text. Trip deletion
   cascades BookingImport and loses the only upstream key/delete-pending flag;
   source cleanup then cannot tombstone that remote result. Cleanup only sets
   flags that require a user to reopen each surviving import. Preserve a minimal
   durable deletion intent (no private candidate content) across trip deletion,
   provide bounded operator retry using authorized credentials, and always
   delete rejected inference results independently of original-file consent.
   Failed remote deletion must stay reviewable/retryable. Test outage, trip
   deletion during extraction, late POST/result, and keep-original rejection.
5. **High: private upstream inference can run in unauthenticated local mode.**
   Settings permit Gemini extraction in local environment with local auth.
   Enforce verified user boundary for any real provider/private inference;
   synthetic fake mode remains an explicit local/test path. Verify missing,
   forged and cross-owner credentials fail before body/provider/storage access.
6. **High: output/review invariants are incomplete.** `_model_output` accepts
   missing fields without adding uncertainty, accepts arbitrary valid zones
   not supported by the source, and can emit repeated candidates from one span
   because its fingerprint includes index. Travel permits duplicate candidate
   IDs and non-completed states with candidates, and never ensures expiry is
   after creation or bounded by the accepted retention. Preserve deterministic
   uncertainty for missing/unsupported facts, conservative field evidence,
   unique IDs/spans, paired schedules and safe terminal envelopes in upstream
   AND travel DTOs. A completed zero-candidate result currently omits
   `duplicate_suggestions`, causing the panel's `.length` to crash. Return a
   consistent typed review shape in every state and test zero candidates,
   malformed states, duplicate IDs, unsupported zones and temporal bounds.
7. **Medium: Phase 6 status choice is missing.** Confirmation hardcodes every
   new reservation to tentative and the UI has no status input. The plan calls
   for explicit tentative/confirmed choice. Add traveler-selected status to
   corrected confirmation and UI; never infer a confirmed authoritative status
   from AI output. Cover replay/fingerprint and both statuses.
8. **High: corrections can silently revert after save/reopen.** Frontend
   `emptyDraft` uses `?? original`, so an explicitly cleared reference/date/time
   is repopulated from the original extraction after saving/reopening. Server
   trip-local projection is calculated from original values before edits,
   displaying stale converted times after corrected dates/zones. Use presence
   semantics for cleared fields and project corrected schedules separately
   while retaining original evidence. Normalize rail/car edits to travel enums
   before PATCH (currently saveCorrections sends unsupported rail/car values).
   Add mounted/component tests for clear/save/reopen and cross-zone correction.
9. **Medium: UI upload/recovery and confirm validation gaps.** A definitive
   invalid-PDF/oversize/changed-key response retains pending body/key and
   permanently disables editing, with no discard/reset action. Keep uncertain
   requests pinned, but offer explicit recovery/cancel after definitive failure.
   Block unresolved required dates/zones/provider/type before confirmation and
   use synchronous mutation guards. Preserve saved-but-refresh-failed recovery.
   Add actual booking-panel tests; the existing 36 frontend tests predate the
   panel and do not verify these paths.
10. **High: conflicting itinerary links in one batch.** Two link-existing
    entries can select the same unlinked itinerary item and different existing
    reservations; checking `reservation_id` misses unflushed relationship edits.
    The second silently replaces the first while both outcomes claim a link.
    Reject conflicting batch assignments using a deterministic planned footprint
    or refreshed relationship state; late failure must roll back everything.
11. **Medium: elapsed bounds and expiry after locks.** Upstream POST receive has
    no elapsed timeout; a trickling authorized body can occupy a request forever.
    Travel extraction has separately renewed parse/SQL/HTTP budgets instead of
    one total bound. Upstream begin/complete writes sit outside inference timeout.
    Use a single per-operation remaining budget with finite worker cleanup and
    safe unknown-outcome fencing. Recheck clock after locks in `_claim` and
    `_save_result` rather than using the pre-lock timestamp. Test slow bodies,
    lock-delayed expiry and both Uvicorn loop modes where clocks matter.

## Integration and maintainability follow-through

Include new private import/source/deletion records in explicit owner migration
safety: either fully scoped backed-up transfer with remote identity invalidation,
or explicit refusal before any partial migration when private records exist.
The current migration table list does not include them. Keep source deletion,
remote deletion intent, and state transitions behind services with a consistent
lock order; split the roughly 1,000-line booking service into concrete lifecycle,
confirmation and projection helpers where it reduces duplicated SQL/state logic.
Do not introduce a workflow engine.

Re-pin the exact reviewed upstream fix revision after implementation. Update
release/ADR/README/AGENTS/checkpoints to distinguish implemented synthetic local
scope from unverified Google/IAM/provider retention/Firestore/Linux/cloud gates.
Run migrated PostgreSQL/concurrency, upstream fake/persisted-adapter tests,
frontend panel/proxy checks and full synthetic mounted plaintext AND PDF flows.
No Phase 7, cloud deployment, personal inbox or real private input.

## Remediation disposition — 2026-10-05

Implementation remediation is committed for coordinator verification. Travel
commit `320201b` includes the fixes below.
The exact upstream fix was committed first at
`ebd00a8e2fb2d8b59a5fb5fa3aa44268e5e79b63`, which is now the travel pin.
The coordinator still needs to independently verify this report and close
Phase 6; no Phase 7 work has started.

1. **PDF and digest identity — addressed.** Travel retains the original byte
   digest and a separate extracted-text digest, sends the latter upstream,
   and validates the result against both stable import identity and literal
   source spans. The mounted production flow uploaded a generated PDF through
   the real upstream request DTO and completed review and confirmation.
2. **Deletion and confirmation credentials — addressed.** The exact proxy
   allowlist forwards verified user credentials for confirm, reject, source
   deletion and authorized cleanup, alongside the independent service
   credential. Backend route tests assert both contexts.
3. **Same-key recovery — addressed.** Durable recovery retains the original
   key and request identity. A pre-dispatch interruption safely retries that
   same key; an unknown outcome reconciles by GET; conclusive pre-acceptance
   client errors become recoverable terminal outcomes instead of an endless
   extracting state. Existing and new tests cover key replay, missing results,
   response loss and expiration fencing.
4. **Durable privacy deletion — addressed.** Rejection queues deletion of the
   upstream result independently of the original-file retention choice.
   Migration `0013` adds a minimal owner/key/hash intent that survives trip
   cascades. Authorized retries are bounded by ten attempts and a 20-second
   total deadline; failure retains the intent for another retry.
5. **Provider authentication — addressed locally.** Real Gemini configuration
   now requires Google OIDC even in a local app environment. Authentication
   middleware rejects missing or forged identity before parsing private body
   content or touching extraction storage. Local fake inference remains
   restricted to explicit synthetic fixtures in local/test mode.
6. **Output and review invariants — addressed.** Upstream removes unsupported
   field values, records strict uncertainty, requires literal evidence, and
   rejects duplicate spans. DTOs validate unique IDs/spans, bounded expiry,
   and candidate-free non-completed states. The travel review shape always
   includes `duplicate_suggestions: []`, including zero-candidate results.
7. **Explicit reservation status — addressed.** The panel requires the
   traveler to select tentative or confirmed for every created reservation.
   The chosen status is sent to confirmation and covered by replay
   fingerprint tests; AI output cannot set authoritative reservation status.
8. **Corrections and projections — addressed.** Explicit null corrections
   remain null after save/reopen. Trip-local schedule values are projected
   from corrected date/time/zone values while original source evidence stays
   available. Rail/car aliases normalize to supported travel reservation
   types before save.
9. **Panel upload and confirmation recovery — addressed.** Definitive invalid
   uploads clear the pinned payload/key and expose reset; uncertain outcomes
   preserve the same key/body. The panel blocks missing required fields,
   requires uncertainty acknowledgement, guards concurrent mutations
   synchronously, and retains saved-but-refresh-failed recovery. Focused panel
   tests cover these behaviors.
10. **Conflicting item links — addressed.** Confirmation precomputes a planned
    itinerary-item-to-reservation footprint and rejects conflicting batch
    assignments before writing. The entire selected batch stays transactional.
11. **Elapsed deadline and lock expiry — addressed locally.** Upstream applies
    one monotonic deadline across body receipt, durable claim, model work and
    terminal persistence, converting the absolute monotonic deadline to a
    relative asyncio timeout so event-loop clock origins cannot diverge.
    Travel applies one extraction deadline across receive, parse, storage and
    upstream operations, and rechecks claim/result expiry after acquiring
    locks. Slow-body, timeout, lock/expiry, and a deliberately shifted
    event-loop-clock regression are included in the local suites. The previous
    Phase 6 identity checkpoint records local HTTP timing checks under Uvicorn
    `auto` and `asyncio`.

**Integration and maintainability:** owner migration explicitly refuses
private source/import/deletion records before partial transfer. Source,
confirmation and deletion state changes live behind services with a
trip-then-import lock order. The large booking service delegates atomic
confirmation to a focused `BookingConfirmationService`; deletion retry has a
separate bounded service and authenticated route. No generic workflow engine
was added.

**Verification evidence:** see the [Phase 6 release record](../releases/phase-6-booking-imports.md)
for exact backend, frontend, mounted synthetic plaintext/PDF checks and
external limits. Travel PostgreSQL/API tests pass 230/230; upstream backend
tests pass 561 with 12 skips; frontend tests pass 42. Both upstream and travel
feature/provider gates remain off. This remediation has not configured Google
OAuth, Cloud Run IAM, Gemini, Firestore TTL, Linux parser enforcement, cloud
storage, or any real private input.
