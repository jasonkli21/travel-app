# Phase 5 local proposal implementation

Date: 2026-10-04
Status: P5.0–P5.5 implementation present for independent review; proposal gates off

## Scope and contract

The travel app now has a local, owner-scoped itinerary proposal lifecycle on top
of the existing revision and deterministic preview groundwork. The accepted
upstream dependency is `personal-ai-system` commit
`6045f004fbdc4887c2bb67da9ae19a571314fc27`, pinned to
`itinerary-proposal-v1`, `travel-itinerary-context-v1`, and
`itinerary-proposal-policy-v2`. Travel uses the documented HTTP client and
imports no upstream Python package or storage schema.

Migrations `0007` and `0008` add portable `itinerary_proposals` storage, an
owner/trip/key unique constraint, downstream reconciliation key, immutable
base snapshot and place-version footprint, lifecycle state, evidence expiry,
stored applied outcome, exact upstream revision, and operation-to-evidence
support references. Migration `0008` backfills existing proposal rows with the
previously accepted upstream revision and empty support. Raw request
instructions are represented by a one-way request fingerprint and are not
persisted. Private booking fields, notes, confirmations, provider payloads,
source references, and travel record IDs are excluded from the AI context.
Context-only requests do not attach research sessions.

Generation requires the trip revision and a stable idempotency key. A timeout
is reconciled using that same key; Travel never automatically retries with a
new key or renews the generation request's external budget after timeout. A
later explicit status read is separately bounded. Applying requires the
expected trip revision and current place footprint. The trip root is locked
before referenced shared places, which are locked in UUID order. Expiry is
checked after dependency locks and immediately before mutation; terminal apply
replay remains available after expiry. Operations, audit state, one trip
revision, and replayable outcome commit atomically. Repeated apply returns the
exact saved outcome. Rejection is explicit. The responsive proposal panel
shows request disclosure, a removable-item allowlist, deterministic full
itinerary preview and diff, warnings, evidence expiry, and revisions. Apply is
separate from generation; stale, expired, invalid, or terminal proposals are
disabled. After an ambiguous apply, the UI reads proposal detail before
recovery; after a committed apply with failed refresh, it closes apply and
blocks edits until reload.

All browser, Travel API, and upstream capability gates default off. The
upstream provider gate is separate. Local owner mode is not authentication;
booking imports, private cloud use, auth, and deployment remain out of scope.
Do not start Phase 6 before independent review closes this implementation.

## Initial verification baseline

The following evidence predates the independent review. The updated remediation
verification below supersedes its listed gaps and records the current results.

| Area | Executed evidence | Result and boundary |
| --- | --- | --- |
| PostgreSQL/API/migrations | `TEST_DATABASE_URL=postgresql+psycopg://jasonkli@127.0.0.1:55433/personal_travel_phase5_test .venv/bin/pytest` from `backend/` | 124 passed, zero skips, one existing Starlette/httpx deprecation warning. This was run with loopback access to the dedicated disposable DB; port 55432 was not used. Includes migrated schema fixtures, populated legacy revision migration, Alembic/ORM parity, and proposal lifecycle tests. |
| Proposal lifecycle | `backend/tests/test_phase5_proposal_lifecycle.py` | Generation preview, idempotency conflict, apply and exact replay, omitted-vs-explicit-null time update, manual edit staleness, shared-place footprint, expired-proposal rejection with unchanged revision, rejection, injected SQL rollback, same-key reservation, concurrent apply, two proposals on one base, apply/reject race, and same-key unknown-outcome recovery passed. |
| Preview and revisions | `backend/tests/test_phase5_proposal_preview.py`, `backend/tests/test_phase5_revision_postgres.py`, migration/review tests | Strict DTO and bound checks, unknown/foreign handles, protected items, deterministic projection, legacy writes, revision preconditions, no-op behavior, populated migration, and DST gap/fold rollback cases passed. The cross-trip opposite-order shared-place lock race was not separately exercised. |
| Python static checks | Ruff check, Ruff format check, `mypy src` from `backend/` | Passed; mypy checked 68 source files. |
| Frontend checks | Pinned pnpm 10.17.1 frozen install; `pnpm test`, `pnpm lint`, `NEXT_PUBLIC_TRAVEL_PROPOSALS_ENABLED=true pnpm typecheck`, and `NEXT_PUBLIC_TRAVEL_PROPOSALS_ENABLED=true pnpm build` from `frontend/` | Passed. Node tests: 20 passed, zero skipped. Lockfile unchanged. |
| Mounted browser behavior | Local Next.js page with a fake loopback Travel API fixture | Verified proposal preview/diff and an apply response lost after commit followed by failed trip refresh. Proposal detail recovered the stored applied result, apply closed, editing stayed blocked, and reload was offered. No real provider or user data was used. This was a focused browser check, not a full assistive-technology audit. |
| Cross-repository HTTP | Migrated disposable Travel schema, Travel API on loopback, and `personal-ai-system` fake generator with memory storage and no credentials; upstream Uvicorn `--loop asyncio` | Passed end to end: proposal became ready, explicit apply committed, replay returned the same stored outcome, and the trip received one new item and one revision. No live provider was contacted. |

Proposal-specific malformed, oversized, or trickling upstream JSON responses,
opposite-trip shared-place lock races, and log-capture privacy assertions were
not separately exercised in this run. The backend enforces response-byte and
deadline rules in code; these unrun cases must remain visible to reviewers
rather than inferred from adjacent research tests.

## Upstream uvloop discrepancy (historical; fixed)

This is the pre-fix reproduction retained for context. Upstream commit
`6045f004fbdc4887c2bb67da9ae19a571314fc27` fixes the clock-domain conversion;
the dual-loop tests and real HTTP results below are the current evidence.

The real fake-backed HTTP flow above passes with Uvicorn's asyncio loop. On
this host, Uvicorn's default `auto` selects uvloop 0.23.0. In that process,
`loop.time() - time.monotonic()` measured `11,162,431.5` seconds. The accepted
upstream proposal service computes `operation_deadline` using `time.monotonic()`
in `backend/src/personal_ai/itinerary_proposals/service.py:167-183`, then passes
`work_deadline` to `asyncio.timeout_at()` at lines 208-210, which interprets an
absolute value in the event loop's `loop.time()` clock domain. With uvloop's
offset the deadline is already in the past: the timeout cancels generation,
`asyncio.timeout_at()` raises `TimeoutError`, and the handler maps it to
`generation_outcome_unknown` at lines 234-240. Its synchronous context helper
also subtracts `time.monotonic()` in `backend/src/personal_ai/context/deadline.py:8-13`.
The Travel client's own deadline correctly uses the running loop clock.

Reproduction uses only local fake services and a disposable Travel database.
From `personal-ai-system/backend/`, start the fake API first with Uvicorn's
default loop, then repeat with `--loop asyncio`:

```bash
ITINERARY_PROPOSALS_ENABLED=true \
ITINERARY_PROPOSAL_GENERATOR=fake \
ITINERARY_PROPOSAL_STORAGE=memory \
uv run uvicorn personal_ai.main:app --host 127.0.0.1 --port 8001 --no-access-log
```

Use the local Travel app with
`PERSONAL_AI_BASE_URL=http://127.0.0.1:8001` and
`PERSONAL_AI_PROPOSALS_ENABLED=true`; create a trip with a candidate, then send
`POST /v1/trips/{trip_id}/proposals` with `X-Expected-Revision`, a UUID
`idempotency_key`, and the UI's context-only request. With default `auto`, the
response is immediately failed with `generation_outcome_unknown`; repeating
with `--loop asyncio` returns a ready preview and the apply/replay flow passes.
Compare the loop clocks directly in the upstream virtual environment:

```bash
uv run python -c 'import asyncio,time; loop=asyncio.new_event_loop(); print(loop.time() - time.monotonic()); loop.close()'
uv run python -c 'import time,uvloop; loop=uvloop.new_event_loop(); print(loop.time() - time.monotonic()); loop.close()'
```

Observed outputs were approximately `-1.25e-7` seconds for asyncio and
`11,162,431.484` seconds for uvloop. In the request trace, the monotonic
deadline from `ItineraryProposalService.create()` is supplied directly to
`asyncio.timeout_at()`; uvloop treats that absolute deadline as already past,
cancels the coroutine, and `asyncio.timeout_at()` converts its own cancellation
to `TimeoutError`. The service catches it and stores
`failure_code="generation_outcome_unknown"`. The failure is thus before the
fake generator can return a proposal, rather than an invalid model result.

Upstream fixed this clock-domain defect in the accepted commit. The travel
repository records both the historical failure and current local verification
so future reviewers can distinguish the former runtime limitation from the
remaining live-provider, cloud, authentication, and production-readiness gates.

## Review remediation verification

The independent review findings and local disposition are recorded in
[`../reviews/phase-5-lifecycle-review.md`](../reviews/phase-5-lifecycle-review.md).
This implementation still awaits independent re-review; Phase 5 is not marked
closed and Phase 6 has not started.

| Area | Executed evidence | Result and boundary |
| --- | --- | --- |
| Travel PostgreSQL/API/migrations | `TEST_DATABASE_URL=postgresql+psycopg://jasonkli@127.0.0.1:55433/personal_travel_phase5_test make backend-test` with `PATH` including `backend/.venv/bin` | 135 passed, zero skips, one existing Starlette/httpx deprecation warning. Run against the dedicated PostgreSQL 16.15 disposable DB with loopback access; port 55432 was not used. Includes populated upgrade/backfill through `0008`, migration/ORM parity, proposal lifecycle and SQL apply tests. |
| Proposal lifecycle/client | `backend/tests/test_phase5_proposal_lifecycle.py` and `backend/tests/test_personal_ai_client.py` | PostgreSQL checks cover lock-wait expiry for apply/detail, terminal replay after expiry, same/cross-day changes, partial times, removal, repeated target operations, full SQL result vs preview, late-operation rollback, opposite-order shared-place locking across two trips, privacy log capture, exact provenance/support persistence, unknown generation without a renewed lookup, and HTTP omission vs explicit-null details by ID/key. Client checks cover malformed, extra-field, wrong-version, oversized, and trickling responses with bounded consumption/elapsed time, one POST, safe failures, and no authoritative mutation. |
| Travel Python static checks | `backend/.venv/bin/ruff check backend`, `backend/.venv/bin/ruff format --check backend`, `PATH="$PWD/backend/.venv/bin:$PATH" make backend-typecheck` | Passed; mypy checked 68 source files. |
| Upstream backend | `PATH="$PWD/backend/.venv/bin:$PATH" make backend-test`, `make backend-lint`, and `make itinerary-proposal-eval` from the accepted `personal-ai-system` checkout | 540 passed, 12 pre-existing manual/provider skips, one Starlette warning; Ruff passed; all 6 synthetic proposal evaluation cases passed with external provider disabled. Upstream source commit: `6045f004fbdc4887c2bb67da9ae19a571314fc27`. The pre-existing modified `frontend/tsconfig.tsbuildinfo` was left untouched. |
| Frontend checks | Pinned Node 24.19.0 and pnpm 10.17.1; `pnpm test`, `pnpm lint`, `NEXT_PUBLIC_TRAVEL_PROPOSALS_ENABLED=true pnpm typecheck`, and `NEXT_PUBLIC_TRAVEL_PROPOSALS_ENABLED=true pnpm build` | Passed in an isolated scratch copy of the checked-in frontend source to keep `.next` separate from the already-running local dev server. Node tests: 20 passed, zero skipped; lint had no warnings; typecheck and production build passed. Lockfile unchanged. |
| Mounted browser behavior | Gated repeatable fixture in [`../../frontend/tests/fixtures/proposal-lifecycle-browser.md`](../../frontend/tests/fixtures/proposal-lifecycle-browser.md), mounting the real `ProposalPanel` on local Next.js | Verified all three scenarios: expiry updates the UI and disables Apply after four seconds; stale detail blocks apply; lost apply response recovers the stored applied outcome and failed workspace refresh blocks edits until reload. The fixture uses deterministic in-memory API methods, with no provider or user data. This is a focused check, not a full assistive-technology audit. |
| Upstream local HTTP | Fake generator, memory storage, local Uvicorn, and checked-in `itinerary-proposal-example.json` request; tested `--loop auto` and `--loop asyncio` | Both loops returned health 200 and proposal POST 201/state `proposed`; both server processes were stopped. No live provider or cloud service was contacted. |

The upstream service retains one monotonic budget for synchronous/storage work
and passes its remaining duration to `asyncio.timeout()` at asynchronous
boundaries. Direct service tests run under asyncio and uvloop, and local Uvicorn
HTTP checks pass for both `--loop auto` (which selects uvloop on this host) and
`--loop asyncio`. This removes the recorded local runtime blocker; it does not
imply live-provider, cloud, authentication, or production readiness.

## Commit sequence

The implementation is checkpointed in these commits:

- `0b87b45` pins the accepted upstream proposal contract in ADR 0010.
- `acd84c1` adds durable proposal lifecycle, client, migration and API.
- `562f4d7` adds lifecycle concurrency and recovery coverage.
- `30ec2f2` verifies an expired ready proposal cannot apply or advance the
  trip revision.
- `f82c2bc` adds the gated proposal review/apply UI and UI-state regressions.
- `6045f004` fixes the upstream proposal timeout clock-domain conversion.
- `5d0cd23` fixes travel lifecycle deadlines, post-lock expiry, and provenance.
- `211eb75` adds HTTP route assertions for omitted and explicit-null operation
  fields in proposal detail.
- `a3bce6c` adds the repeatable mounted browser fixture, live expiry gating,
  and the upstream provenance API fields.
- The final documentation/checkpoint commit follows this release record.

Independent review must assess atomic apply/replay, all mutation revision
paths, footprint completeness and lock ordering, privacy, and whether the
default-off gates remain safe. Phase 5 remains pending independent re-review.
