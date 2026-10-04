# Phase 5 local groundwork checkpoint

**Status:** P5.0–P5.2 implemented locally; review findings remediated; Phase 5 incomplete
**Date:** 2026-10-03  
**Start baseline:** `dc53d25` on `codex/phase-0-scaffold-corrections`  
**Plan:** [`phase-5-implementation-plan.md`](../phase-5-implementation-plan.md)  
**Policy:** [`0010-phase5-proposal-safety.md`](../decisions/0010-phase5-proposal-safety.md)

## Contract gate

The user directed a read-only inspection of `../personal-ai-system`. At that
inspection its branch `codex/phase-6-decision-support` was at
`0c397dcd92d8503581c0727a6da9a0fbadfe3e6f`, equal to `origin/main`. Its
versioned contracts cover research, decision support, domain comparison, and
iterative research. The API source and contract document have no typed
itinerary-patch route, DTO, capability gate, or fake fixture. Decision
candidate claims and follow-up query suggestions do not constitute travel
operations. No upstream file was changed, and this work did not invent or call
an itinerary-proposal API.

ADR 0010 defines the permitted eventual operation and privacy policy. It is
accepted for local groundwork only; it does not accept an upstream contract or
authorize generation, persistence, apply, rejection, or proposal UI.

## Delivered groundwork

- Migration `0006` adds nonnegative integer revisions to trips and shared
  places, initialized to zero, and keeps the SQLAlchemy models in parity.
- Existing trip and shared-place write routes accept optional
  `X-Expected-Revision`. Supplied stale values return stable 409
  `stale_revision` details after the aggregate root is locked and before any
  mutation. Omitted headers preserve legacy compatibility while actual writes
  still advance revisions.
- Revision accounting covers trip fields, inclusive day/date/timezone
  reconciliation, itinerary item create/update/delete/move, reservations,
  saved-place links and notes, manual candidates, and Geoapify candidate
  imports. Same-value patches, empty patches, same-position moves, and imports
  that add no candidate do not bump a revision. Shared-place edits bump only
  that place's revision and do not fan out trip writes.
- The trip UI sends revisions for all trip writes and each place's own revision
  for independent place edits. The same-origin proxy explicitly forwards only
  `Content-Type` and `X-Expected-Revision`; absent revision headers remain
  absent. A stale or ambiguous write blocks further edits. After a successful
  explicit recovery reload, open edit forms close and all mounted form drafts
  reset to the newly loaded snapshot. Ordinary refreshes keep in-progress
  drafts; quick-place creation was verified to preserve its parent item draft.
- Internal strict frozen DTOs describe at most 25 operations: add from an
  existing trip candidate, move, set/clear local times, and explicitly
  allowlisted optional-item removal. Opaque handles are mapped in a bounded
  owner/trip-correlated projection. Private notes, confirmation codes, source
  references, and provider payloads are omitted.
- The pure preview computes every day's final order and local schedule, reuses
  existing DST and reservation-overlap rules, protects booked/completed and
  confirmed-reservation anchors, and returns a deterministic place revision
  footprint. It cannot write authoritative state and has no API route.

## Logical commits

1. `76da3b2` — `docs: define Phase 5 proposal safety policy`
2. `9c99580` — `feat: add aggregate revision preconditions`
3. `e3070b4` — `feat: add bounded proposal preview groundwork`
4. `7b5b4d0` — `fix: stabilize proposal preview warning order` (sort warnings
   by stable reservation IDs; includes a regression for randomly assigned
   opaque handles).
5. `a1cece6` — documentation checkpoint for the initial groundwork release.
6. `dd0974c` — `fix: preserve revision conflicts through web recovery`.
7. `ea1576b` — `test: add stale recovery browser fixture`.
8. Review checkpoint — findings, remediation, browser steps, and updated
   verification evidence are recorded after the fix commits.

## Verification

- `TEST_DATABASE_URL` pointed to a disposable PostgreSQL 16.15 cluster on
  loopback port 55433. The full backend suite passed: **110 passed, 0 skipped**.
  The migrated fixtures run `alembic check`; legacy upgrade tests exercise
  populated rows. Tests cover all revision mutation families, no-op behavior,
  stale 409s, two concurrent same-revision writes, a post-flush rollback,
  shared-place revisions across trips, and DST-gap/fold rollback.
- `ruff check src tests`, `ruff format --check src tests`, and `mypy src`
  passed.
- With pinned pnpm **10.17.1**, the frozen offline lockfile install passed and
  reused all 340 packages from `/private/tmp/travel-review.ULKz0d/pnpm10-store`.
  The existing install policy reported `unrs-resolver` as an ignored build
  script; no script allow/deny policy was changed. The exact install command
  was:

  ```bash
  CI=true /Users/jasonkli/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node \
    /private/tmp/travel-review.ULKz0d/corepack/v1/pnpm/10.17.1/dist/pnpm.cjs \
    --dir frontend install --frozen-lockfile --offline \
    --store-dir=/private/tmp/travel-review.ULKz0d/pnpm10-store
  ```
- After remediation, the frontend suite passed: **18 Node tests, 0 failures**;
  ESLint, Next route type generation plus strict TypeScript, and the Next
  production build also passed. The new request regression ran under Node
  22.23.3 (the CI major version) and exercised the typed DELETE and shared-place
  PATCH clients through the same-origin proxy to parsed stale 409 recovery. It
  verified the allowlist and omitted-header compatibility for manual place
  creation.
- A mounted Chrome regression used the checked-in
  [`revision-recovery-api.mjs`](../../frontend/tests/fixtures/revision-recovery-api.mjs)
  fixture with Next dev on loopback and no travel database. Starting at trip
  revision 4 with `Old museum title`, it submitted an edited item, received
  `stale_revision` with current revision 5, and reloaded. The form closed; when
  reopened it showed `Remote museum change`. A follow-up quick-place creation
  left an unrelated in-progress item title draft intact and added the place to
  the place selector. Repeatable startup and interaction steps are in the
  [independent review record](../reviews/phase-5-groundwork-review.md).
- The backend suite emitted one existing Starlette/httpx deprecation warning.
  Backend code was unchanged by the remediation, so the previously recorded
  110-pass/zero-skip migrated PostgreSQL run was not repeated. No live AI,
  Geoapify, hosted CI, cloud service, or upstream proposal contract was
  exercised.

## Remaining gates

Phase 5 is not complete. The two independent-review findings are remediated;
the contract gate remains open. Work must remain local-only until
`personal-ai-system` accepts a versioned proposal
contract and capability gate. Subsequent work still needs durable owner-scoped
proposal storage and expiry, idempotent create/replay, deterministic apply
revalidation and one-transaction apply/replay, explicit rejection, a separately
disabled generation client, and review/apply UI with recovery tests. Authentication,
booking imports, and cloud deployment remain outside this stage.
