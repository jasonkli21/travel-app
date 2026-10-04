# Phase 5 implementation plan — structured AI proposals

**Status:** P5.0–P5.5 implemented and independently reviewed locally; gates default off
**Date:** 2026-10-04
**Baseline:** `27cc9b1`, reviewed local Phase 0–4 plus P5.0–P5.2 groundwork
**Roadmap:** [phased implementation plan](09-implementation-plan.md)
**Prerequisites:** [audit](reviews/phase-0-4-audit.md), ADRs 0003, 0008 and 0009

## Goal and scope boundary

Let a traveler request a small typed itinerary proposal, inspect an accurate
before/after preview, and explicitly apply it once through deterministic travel
services. The proposal must not overwrite intervening edits or treat expired
evidence as current. Manual workflows remain authoritative and usable with AI off.

Deliver aggregate/place versions, a bounded accepted proposal contract, durable
travel-owned proposal lifecycle, deterministic preview/revalidation, atomic
apply, explicit rejection and minimal audit metadata.

Defer booking/extraction, reservation creation or cancellation, autonomous
actions, background retries, money movement, collaboration, a general patch
language, arbitrary SQL/URLs, provider-side booking and cloud deployment.
Limit the first proposal to one trip. Phase 5 remains local; private booking
fields/notes are excluded from AI context. Authentication remains mandatory
before Phase 6 imports or any private hosted use.

## Decisions and external gates

| Concern | Decision |
| --- | --- |
| Upstream capability | Accepted at `personal-ai-system` revision `6045f004fbdc4887c2bb67da9ae19a571314fc27`. Pin `itinerary-proposal-v1`, `travel-itinerary-context-v1`, and `itinerary-proposal-policy-v2`; consume only the documented HTTP routes and strict DTOs. Keep travel, upstream capability, and provider gates independent and off by default. |
| Operation set | Add an item using an existing reviewed place/candidate; move an item; update local times; remove an optional item. No mutation of places, reservations or confirmed/required anchors. Initially bound a patch to 25 operations; finalize that limit with the accepted contract. |
| Required vs optional | The current status enum is not an optionality flag. Initially permit removal only for unlinked, non-booked/non-completed items explicitly selected as removable by the user in this request. Persist that handle allowlist with the proposal. Moves/time edits cannot alter booked or confirmed reservation anchors. Do not infer optionality or add a new general protection model in this slice. |
| Versioning | Add a nonnegative monotonically increasing trip revision and reusable-place revision. Every authoritative aggregate mutation increments the trip revision, including day/reservation/candidate edits; every place metadata edit increments its place revision. Root locks still protect SQL invariants. Timestamps are not concurrency tokens. |
| Shared places | A proposal captures revisions for all shared place dependencies it used. Do not fan out revision updates across every referencing trip. Apply locks the trip, then referenced places in deterministic ID order, and checks the footprint before mutation. |
| Preview | Persist a validated immutable operation list and base footprint. Preview is computed by travel rules, including order, destination dates, DST, links and conflicts. AI prose cannot substitute for a deterministic diff. No silent rebase. |
| Apply | Require explicit apply, matching base/current footprint and unexpired evidence. Apply operations plus proposal state/audit/revision once in one transaction. Repeated apply returns the stored outcome; it does not execute again. |
| Failure | Malformed/upstream-disabled/expired/stale proposals never change authoritative state. A timeout is not permission for an automatic retry with a new downstream key. |
| Audit | Persist only typed operations, base footprint, lifecycle, source capability/version, necessary evidence references/expiry and application result. Exclude raw prompts, private notes and provider payloads from routine logs. |

An ADR must record the explicit-removal/anchor policy, accepted contract, revision
footprint, evidence expiry and replay semantics before enabling generation.
ADR 0010 records these decisions. The runtime issue and independent review
findings are resolved locally; capability and provider gates default off.

## Current delivery checkpoint

The P5.0–P5.2 groundwork and P5.3–P5.5 implementation are present locally.
The earlier groundwork review remains in
[`releases/phase-5-groundwork.md`](releases/phase-5-groundwork.md); the current
scope and exact verification are in
[`releases/phase-5-local-proposals.md`](releases/phase-5-local-proposals.md).
The accepted upstream contract and safety policy are pinned in ADR 0010. The
proposal API, storage, lifecycle, gated client, and UI are implemented. The
stage has passed independent local review. Keep all proposal gates off by default.

## Travel API contracts

These are implemented travel-side routes. The upstream HTTP contract remains
independent and is not exposed through the travel API.

| Method | Travel route | Behavior |
| --- | --- | --- |
| POST | /v1/trips/{trip_id}/proposals | Owner-scoped bounded request, mandatory expected revision and idempotency key; capture immutable context, release SQL, call the accepted capability, validate, then persist ready or safely recoverable generation state. |
| GET | /v1/trips/{trip_id}/proposals/by-key/{idempotency_key} | Reconcile a timed-out generation by the same owner/trip key; never create a new key for automatic retry. |
| GET | /v1/trips/{trip_id}/proposals/{proposal_id} | Return typed operations, deterministic diff, base/current revisions, expiry and ready/stale/expired/applied/rejected presentation. |
| POST | /v1/trips/{trip_id}/proposals/{proposal_id}/apply | Require expected revision; revalidate footprint, rules and expiry; apply once atomically and return stored applied outcome on replay. |
| POST | /v1/trips/{trip_id}/proposals/{proposal_id}/reject | Idempotently reject a ready proposal without changing itinerary state. Applied proposals cannot be rejected. |

Add revision fields to existing detail responses. Updated UI writes send an
expected revision; stale preconditions return a stable 409 and reload/review
path. The proxy forwards only the explicitly supported precondition. After a
successful explicit stale/uncertain recovery reload, discard open form drafts
before enabling edits against the new snapshot; ordinary refreshes preserve
unrelated drafts. Preserve old manual client payloads initially by allowing an
omitted precondition, while **still incrementing revisions on every write**.
Document legacy last-writer semantics; proposals always require a footprint.
Avoid turning existing collection responses into a breaking pagination
envelope.

Context projection sends only necessary dates/timezone, local schedules,
user-selected labels and opaque proposal-scoped handles. Travel maps those
handles back to owner-owned IDs server-side. Exclude confirmations, source
references and notes. The contract must separate input context from untrusted
model instructions and enforce operation enums, field lengths/counts, strict
extra-field rejection and valid handle correlation.

Evidence references identify observations and expiry, not permanent opening
hours or availability. Require explicit handling of uncited/insufficient work.
Generation and result detail reads remain under the bounded external deadline;
response bytes are bounded before parsing.

## Data and service design

Migration `0007` adds the travel-owned `itinerary_proposals` table (migration
`0006` added revisions); migration `0008` adds exact upstream revision and
operation-to-evidence provenance for existing and new rows:

- UUID, owner_id, trip_id FK, schema and policy versions, support mode, opaque
  trip handle, and upstream proposal ID;
- request idempotency key and request fingerprint, unique per owner/trip/key;
- immutable base trip revision and place-version footprint;
- validated bounded operations and minimal evidence-reference metadata;
- immutable base snapshot, created/expiry time, generating/unknown/ready/
  failed/applied/rejected lifecycle, applied revision and minimal replayable
  outcome.

The migration uses conventional checked states and portable JSON for bounded
validated operations and snapshots. Stale/expired status is derived from the
footprint/time; malformed output cannot become ready. Reusing a key with
different request content returns a conflict. Proposal rows cascade with the
trip, and stored snapshots exclude raw prompts and private booking data.

Refactor transaction-owning public services only enough to share concrete
validation/mutation helpers with a single proposal-apply transaction. Do not
call several public methods that each commit, nest begin() blindly, or bypass
existing ordering/time/link rules. Preview uses the same deterministic rule
definitions on an immutable projected state; apply repeats validation against
locked SQL state. No generic unit-of-work/plugin framework is needed.

Snapshot acquisition and proposal persistence are short independent
transactions. After generation, compare current dependencies again: a proposal
may be stored as stale for review, but cannot be presented as currently
applicable. Shared places, reservations and candidates used by the projection
must be represented in the version footprint even when no patch edits them.

## Frontend behavior

Add a focused proposal section with bounded request scope/disclosure, pending
and unavailable states, operation-by-operation diff, required-anchor warnings,
source expiry, and visible base/current revision.

Apply is a separate explicit action after preview. Disable it for stale,
expired, malformed, unsupported or already terminal work. On a 409, preserve
the proposal for inspection and offer reload/new generation; do not auto-rebase
or reapply. Guard same-turn submission. A committed apply followed by failed
refresh reports success, closes the apply action and blocks editing until reload.
After an ambiguous network outcome, read proposal detail before offering retry.

## Dependency map and work packages

~~~text
P5.0 ADR + accepted upstream contract
             |
P5.1 revisions + manual mutation coverage
             |
P5.2 typed proposal validation + deterministic preview
             |
P5.3 storage + one-transaction apply/replay
             |
P5.4 gated generation client + UI
             |
P5.5 verification + release
~~~

### P5.0 — Close the contract and policy

Record the upstream revision and fake fixtures; choose protected/removable
semantics, evidence policy and retention. Document safe local-only context.

**Acceptance:** reviewers can identify every permitted operation, handle,
deadline, expiry and replay rule; generation cannot be enabled against an
unaccepted contract.

### P5.1 — Add revision accounting

Migrate existing trips/places, extend responses and UI preconditions, and
increment revisions in every mutation path, including imports/manual
candidates and timezone/date reconciliation. Define no-op policy consistently.

**Acceptance:** concurrent manual updates detect supplied stale preconditions;
legacy writes still invalidate proposals; shared place edits invalidate only
dependent proposals without fan-out locks.

### P5.2 — Validate and preview patches

Implement strict typed operation DTOs, handle mapping, bounded state projection
and deterministic simulation. Validate same-trip ownership, protected anchors,
destination order/time/DST and candidate existence.

**Acceptance:** invalid operations cannot reach mutation; the diff accurately
represents the entire resulting itinerary and warnings.

### P5.3 — Persist and apply once

Implement owner-scoped repository/service, idempotent creation/rejection and
one root-locked apply transaction. Recheck footprint/expiry inside locks.
Store the applied outcome in the same transaction.

**Acceptance:** stale or failed batches change nothing; concurrent/replayed
apply creates at most one result; SQL failure rolls back operations/audit/revision.

### P5.4 — Integrate accepted generation and UI

Extend PersonalAIClient using the accepted contract and separate off-by-default
gate. Use bounded streamed reads and immutable SQL snapshots. Add review/apply
UI and focused browser/component tests for stale previews and refresh failure.

**Acceptance:** AI off leaves manual planning intact; output is reviewed and
explicitly applied, never silently imported.

### P5.5 — Record release evidence

Update architecture/data/AI guides, versions/compatibility policy, migrations,
local setup, handoff and release note with exact commits/checks/gates.

**Acceptance:** planned external work and actually delivered behavior are
distinguished; no booking/auth/cloud feature is accidentally claimed.

## Failure and verification matrix

| Area | Required cases |
| --- | --- |
| Migration/version | Upgrade from 0005, populated legacy records, constraints, downgrade limits, ORM parity; revision increments for every manual/provider path and rollback/no-op policy |
| Contract | Unknown operation/extra field/handle, oversized body/list, malformed version, foreign trip/owner/candidate/place, invalid dates/timezone, uncited/expired evidence |
| Preview/apply | Add/move/time/remove, repeated target operations, same-day/cross-day order, DST gap/fold, protected anchor/link, conflicting reservations, rollback after a later operation fails |
| Concurrency | Manual edit between generation/preview/apply; shared place edit; two apply requests; two proposals on one base; opposite-trip place locking; request replay with differing fingerprint |
| Failure recovery | Disabled capability, malformed/oversized/trickling response, unknown remote outcome, apply response loss, failed post-commit refresh, reject vs apply race |
| Privacy/UI | No booking fields/notes sent or logged; safe citations; accurate diff, keyboard/mobile access, explicit apply; stale/expired/applying/applied states |

Run complete migrated PostgreSQL/API/concurrency suites, Ruff/mypy,
frontend lint/types/Node tests/build, migration parity and package checks.
Use fake accepted HTTP fixtures by default; record live upstream verification
separately. Introduce UI integration tooling only to cover real state/failure
cases, not to mirror implementation details.

## Commit sequence and exit gate

1. docs: accept Phase 5 contract and revision/protection ADR.
2. feat: add proposal-safe revisions and manual preconditions with migrations/tests.
3. feat: validate, preview and atomically apply persisted proposals.
4. feat: integrate gated proposal generation and review UI.
5. docs: record release and remediation evidence.

The Phase 5 implementation has passed independent local review. The release
record separates each executed check from unrun cases and runtime limitations.
Keep generation gated until the uvloop deadline issue and review gate are
closed. Lessons for Phase 6: reuse version/replay and safe transaction
primitives, not the itinerary operation schema for reservations.
