# Phase 5 independent lifecycle review

Review baseline: travel `a684e5c`; upstream accepted local contract `8535cad`.
Coordinator independently ran the migrated PostgreSQL suite: 124 passed, zero
skips; Ruff check/format and mypy passed. Implementation remains under review.

## Required fixes

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
