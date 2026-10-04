# Phase 5 local groundwork checkpoint

**Status:** P5.0–P5.2 implemented locally; independent review pending; Phase 5 incomplete  
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
  for independent place edits. A stale response blocks further editing until
  the traveler reloads the workspace; a mocked real API request test verifies
  the 409 parsing and recovery path.
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
5. Documentation checkpoint — this release/handoff/status record follows the
   implementation commits in Git history.

## Verification

- `TEST_DATABASE_URL` pointed to a disposable PostgreSQL 16.15 cluster on
  loopback port 55433. The full backend suite passed: **110 passed, 0 skipped**.
  The migrated fixtures run `alembic check`; legacy upgrade tests exercise
  populated rows. Tests cover all revision mutation families, no-op behavior,
  stale 409s, two concurrent same-revision writes, a post-flush rollback,
  shared-place revisions across trips, and DST-gap/fold rollback.
- `ruff check src tests`, `ruff format --check src tests`, and `mypy src`
  passed.
- Frontend ESLint, Next route type generation, TypeScript `tsc --noEmit`, and
  all **17 Node tests** passed.
- Next.js production standalone build passed. The packaged server started and
  served `/` with HTTP 200. Its `/api/health` returned 503 because the travel
  API was intentionally not started for the web package smoke check.
- The lockfile install resolved all 340 packages from the local cache and
  passed the package supply-chain policy for 398 entries. The installed pnpm
  version exited nonzero at its build-script gate for `unrs-resolver`; no
  build-script allow/deny policy was added. Equivalent frontend checks and the
  production build ran directly with the bundled Node executable.
- The backend suite emitted one existing Starlette/httpx deprecation warning.
  No live AI, Geoapify, hosted CI, cloud service, or upstream proposal contract
  was exercised.

## Remaining gates

Phase 5 is not complete. Independent review of these commits is pending. Work
must remain local-only until `personal-ai-system` accepts a versioned proposal
contract and capability gate. Subsequent work still needs durable owner-scoped
proposal storage and expiry, idempotent create/replay, deterministic apply
revalidation and one-transaction apply/replay, explicit rejection, a separately
disabled generation client, and review/apply UI with recovery tests. Authentication,
booking imports, and cloud deployment remain outside this stage.
