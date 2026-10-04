# Phase 5 independent lifecycle review

Review baseline: travel `a684e5c`; upstream accepted local contract `8535cad`.
Coordinator independently ran the migrated PostgreSQL suite: 124 passed, zero
skips; Ruff check/format and mypy passed. Implementation remains under review.

## Current remediation disposition (2026-10-04)

Local remediation is committed in travel `5d0cd233f60f6adaf925a8f636daa50b1dfe7b57`,
`211eb7564e86b77bebcbfb0c2476dc54ec2042bb`, and
`a3bce6c` (repeatable browser fixture and live expiry gating), plus upstream
`6045f004fbdc4887c2bb67da9ae19a571314fc27`. The source fixes and acceptance
checks below are complete locally; Phase 5 remains open pending independent
coordinator re-review.

| Finding or acceptance gap | Local disposition and evidence |
| --- | --- |
| Upstream monotonic vs event-loop clock domains | Upstream async timeout boundaries now pass the remaining duration from the shared monotonic budget. Service test runs with asyncio and uvloop; Uvicorn HTTP POST returns 201/state `proposed` under both `auto` and `asyncio`. Upstream suite: 540 passed, 12 pre-existing manual/provider skips; Ruff and six-case synthetic proposal evaluation passed. |
| Expiry while waiting for trip/proposal/place locks; terminal replay | Clock reads for detail and mutation now occur after dependency locks; apply checks expiry immediately before mutation. Terminal applied replay stays ahead of expiry checks. Deterministic lock-wait tests verify expired detail/apply and unchanged SQL state/revision; replay after expiry succeeds. |
| Generation timeout caused by a renewed by-key budget | Initial generation returns the persisted unknown detail without calling automatic reconciliation again. A later explicit status route remains bounded. Tests assert one POST and one initial GET only, then verify the later explicit status read. |
| Omitted vs explicit-null operation fields | Proposal detail routes exclude unset fields recursively. Real route tests through create, detail-by-ID, and detail-by-key assert omitted endpoints stay omitted while explicit null clears remain JSON null. |
| Proposal client malformed, wrong-version, extra, oversized, and trickling responses | Proposal-specific MockTransport tests check bounded response reads and elapsed time, one POST, safe errors, no private response/log text, and unchanged authoritative trip data. |
| SQL apply vs full preview and rollback | PostgreSQL test applies ordered same-day/cross-day moves, partial schedule updates, repeated target operations, add, and selected removal; it compares full persisted itinerary with preview. A later injected SQL failure rolls back earlier item changes and proposal state. |
| Opposite candidate order across two trips; privacy capture | Concurrent applies for two trips sharing the same places in reverse candidate order verify sorted shared-place lock SQL and successful revisions. Failure test captures logs and confirms private upstream details are absent. |
| Durable provenance | Migration `0008` backfills prior proposal rows, enforces `upstream_revision` and `operation_support`, and passes upgrade/parity checks. HTTP detail exposes both values; tests verify persisted exact upstream revision and evidence handles. |
| Repeatable mounted browser coverage and live expiry button | Checked-in local fixture mounts the actual `ProposalPanel` with deterministic API methods. Browser verified expiry disables Apply after four seconds, stale detail blocks apply, and lost apply response recovers applied state while failed refresh blocks edits until reload. Instructions are in [`../../frontend/tests/fixtures/proposal-lifecycle-browser.md`](../../frontend/tests/fixtures/proposal-lifecycle-browser.md). |

Current travel verification: `TEST_DATABASE_URL=postgresql+psycopg://jasonkli@127.0.0.1:55433/personal_travel_phase5_test make backend-test` passed 135 tests with zero skips; Ruff check/format and mypy passed. Frontend tests passed 20/20; lint, typecheck, and production build passed. See the [Phase 5 release record](../releases/phase-5-local-proposals.md) for exact environment boundaries and current evidence.

## Baseline required fixes

1. **Upstream clock domain (high).** Proposal service creates deadlines from
   `time.monotonic()` but supplies them to `asyncio.timeout_at()`, which uses
   the running loop clock. Coordinator independently reproduced the uvloop
   offset of 11,162,431.484 seconds. Keep synchronous/storage deadlines in their
   intended monotonic domain and convert to a remaining duration at asynchronous
   timeout boundaries, without granting fresh budgets. Verify both loops.
2. **Expiry after lock waits (high).** Travel `_apply_tx` captures `now` before
   trip/proposal/place locks; an initially fresh request can wait until evidence
   expires and still mutate. Read the clock after all dependency locks and
   immediately before mutation. Preserve already-applied replay irrespective of
   expiry. Detail presentation should also use a fresh post-lock clock. Add a
   deterministic lock-wait regression with unchanged SQL state/revision.
3. **Generation deadline reset (high).** After client create/reconciliation
   consumes its entire budget, `generate` marks unknown and calls `get`, which
   automatically runs another full-budget by-key lookup. With the 45-second
   default this can exceed the 60-second browser proxy deadline. A single
   generation request must not renew its external budget; return persisted
   unknown detail without another lookup or pass the same absolute budget.
   Later explicit status reads may have their own bounded lookup.
4. **Partial-operation HTTP semantics (medium).** Persisted operations preserve
   omitted time endpoints, but `ProposalDetailResponse.operations` serializes
   the local DTO defaults, emitting omitted endpoints as null. Coordinator
   reproduced `start_time`-only serialization with `end_time: null`. Preserve
   omission in travel HTTP detail/replay serialization, including explicit null
   clears, and test through real route responses.

## Acceptance gaps to close

- Add proposal-specific malformed/extra-field/wrong-version, oversized and
  trickling HTTP response tests, verifying bounded reads/elapsed time, safe
  errors, one POST, and no authoritative mutation.
- Exercise SQL apply for add, same/cross-day move, remove, partial times, repeated
  target batches and rollback after a later operation, checking that persisted
  itinerary exactly matches preview (not just pure simulation).
- Exercise two trips sharing multiple places in opposite candidate order to
  verify deterministic place locking, and capture privacy logs on failure.
- Preserve operation-to-evidence support references and exact upstream revision
  provenance in durable proposal metadata/detail where needed for later audit;
  current storage drops `operation_support` and contains no upstream revision.
  Use a follow-up migration for already-applied `0007`, not a silent schema edit.
- Provide repeatable mounted UI fixture/evidence; the prior mounted check was
  temporary. Verify expiry gating as time passes and stale/ambiguous recovery.

Re-review fixes and run relevant complete checks before closing Phase 5 or
starting Phase 6. No live provider, authentication or cloud readiness is implied.

## Coordinator closure of Phase 5 local scope

Independent re-review closed all substantive findings. A final small fix prevents
renewed reconciliation budgets for upstream running results as well as exceptions;
failed results carry no dangling evidence support. The upstream loop regression
no longer assumes a platform-specific clock offset (upstream commit `96cf73b`);
the accepted runtime contract pin remains `6045f004fbdc4887c2bb67da9ae19a571314fc27`.

Coordinator verification: 136 travel PostgreSQL tests passed, zero skips; Ruff
check/format and mypy passed. Upstream 540 tests passed with 12 existing
manual/provider skips, and Ruff passed. Frontend 20 tests, ESLint, generated
types/TypeScript and production build passed. Migration 0008 and repeatable
mounted fixture coverage were re-reviewed. No substantive residual finding
remains in the local Phase 5 scope. Default-off provider/capability gates,
live provider quality, hosted authentication and cloud verification remain
separate; no live service or deployment was performed.

Next: assess Phase 6 identity, secure source lifecycle and accepted extraction
prerequisites; delegate one logical stage at a time to fresh Luna Extra High
agents. Preserve the upstream pre-existing frontend build-info modification.
