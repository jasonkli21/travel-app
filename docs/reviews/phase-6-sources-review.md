# Phase 6 P6.2 independent review

Reviewed `af844ff`–`c71c55f` against the Phase 6 plan and system invariants.
Identity review remains closed. P6.2 requires the following substantive fixes
before extraction work; no private-input gate is accepted as live-ready.

1. **Keep imports/replay after source deletion.** `source_cleanup._finish_deletion`
   deletes every BookingImport referencing a source, losing durable request/hash
   identity and future confirmed outcomes. Source deletion/expiry must remove raw
   bytes while retaining import lifecycle/replay metadata (nullable reference or
   retained minimal deletion tombstone). Do not let an old key/hash create a new
   authoritative outcome after cleanup. Confirm/review will use this contract.
2. **Enforce retention on access.** Metadata/download/re-upload accept expired
   ready sources until the optional operator cleanup runs. Fail source access at
   expires_at, without deleting outcome metadata. Missing/corrupt bytes must fail
   consistently; ready existence checking currently validates modes/size only,
   while hash checking occurs only in download. Document source availability.
3. **Bind every accepted request key.** Hash dedupe returns an existing import
   for a new request key without persisting that key. A later different document
   with the same new key is accepted. Persist request-key aliases/fingerprints
   with SQL uniqueness, or reject a new-key hash duplicate with documented
   original-key recovery. Test concurrent aliases and changed content.
4. **Serialize promotion/deletion/cleanup transitions.** Upload may mark pending
   source ready after a concurrent DELETE/cleanup marks it deleting. Cleanup
   reads stale ORM rows without row locks/state predicates and can resurrect a
   deleting row or remove an active import. Use fixed lock order and refreshed
   conditional short transactions; bytes remain outside long SQL waits. Test
   pending promotion racing deletion and concurrent cleanup/deletion.
5. **Bound cleanup fairly.** A limited ready scan always inspects the oldest
   healthy sources, starving later missing objects forever. Add a stable scan
   cursor/checkpoint or document operator pagination so repeated bounded passes
   eventually cover all metadata/files. Do not use successful deletions as the
   only work counter; enforce inspected-record and elapsed limits.
6. **Move SQL/domain lifecycle out of async route handlers.** imports.py embeds
   the whole SQL/storage workflow and synchronous SQL/fsync on the ASGI event
   loop. Use a concrete source service/repository consistent with existing
   architecture, worker-owned sessions for synchronous database work, bounded
   I/O offloading, short transactions. Prove a blocked upload SQL/fsync cannot
   stall unrelated requests; no Session crosses concurrent threads.
7. **Finish boundary/safety coverage.** Existing 10 source tests lack actual
   slow/chunked upload/cancellation receive tests, post-promotion SQL failure,
   pending recovery and concurrent deletion/cleanup. Auth-before-body test must
   use a receive/stream that fails if touched, rather than only checking 401.
   Memory-kill test mocks RSS rather than causing bounded measured allocation;
   add real isolated allocation/decompression-limit evidence on supported host
   and Linux/container claims must remain unverified until actually run.

Frontend source passthrough additionally clears the abort timer before its
stream is consumed and lacks a bounded byte/elapsed/cancellation wrapper.
Wrap source streams with the known maximum and end-to-end deadline, clean up
readers and cancel on downstream disconnect; test an endless/oversized body.
Keep all existing 64KiB JSON behavior and explicit credential allowlists.

## Implementation response — local remediation checkpoint

The following changes implement the requested repairs on the local P6.2 branch.
At this remediation checkpoint, whole-Phase 6 review remained pending. It later
closed locally; see the [combined Phase 6 review](phase-6-whole-review.md).

1. **Replay survives byte deletion.** Migration `0011` makes
   `booking_imports.source_id` nullable with `ON DELETE SET NULL` and stores
   media type and byte size on the import alongside request key/fingerprint and
   source hash. Deleting/expiring source bytes no longer deletes the import.
   Same-key retries recover that durable metadata after deletion.
2. **Expiry is enforced at access time.** Metadata reports `expired` at the
   seven-day deadline and downloads return 410 independent of cleanup cadence.
   Source details and downloads verify stored byte size and hash; cleanup later
   removes unavailable bytes without erasing the replay record.
3. **No implicit request-key aliases.** Owner/trip request-key and source-hash
   unique constraints arbitrate concurrent requests. Reusing a key with
   different content conflicts; submitting a known hash with a new key returns
   409 and instructs recovery through its original key. Concurrent key/hash
   races have SQL-backed tests.
4. **Transitions are conditional.** Promotion can mark only a pending,
   unexpired source ready. Delete/cleanup first conditionally transition the
   source to deleting, remove bytes outside SQL transactions, then conditionally
   delete the source row. The source row is the first mutated lifecycle record;
   tests cover promotion-vs-delete and cleanup-vs-delete races.
5. **Cleanup scans fairly.** A single UUID cursor pages all source metadata,
   advancing over healthy rows as well as damaged rows. A sorted orphan-file
   cursor independently pages local storage. Each pass caps inspected records
   (1–1000) and checks its elapsed-work budget (0.1–60 seconds); the CLI prints
   continuation cursors for repeated operator passes.
6. **SQL and storage leave the async event loop.** `SourceLifecycleService`
   owns short sessions for each synchronous lifecycle operation. Upload file
   writes, fsync, reads, promotion, parser invocation, and close work run in
   worker threads. A blocked fsync and a blocked SQL operation each leave
   `/health` responsive; tests assert a Session never crosses its owning worker.
7. **Boundary evidence is executable.** Tests exercise a direct ASGI receive
   that fails if unauthenticated upload touches the body, slow chunked receive
   timeout, disconnect/temp cleanup, post-promotion SQL failure and pending
   recovery, races, and a spawned child that page-touches allocations until the
   macOS production RSS watchdog terminates it. Only this macOS host was tested;
   Linux/container memory enforcement remains unverified.
8. **Frontend download is bounded end-to-end.** The exact source route is
   streamed through a 10 MiB byte cap and 30-second deadline that remains active
   through body consumption. Oversize, timeout, and downstream cancellation
   cancel the reader. The ordinary 64 KiB JSON cap and credential allowlists
   remain in place.

Verification on the local PostgreSQL 16 test database (port 55433): **205
backend tests passed, zero skipped**, including all 19 source-lifecycle tests
and migration parity. Ruff check/format and mypy passed (81 backend source
files). Pinned pnpm 10.17.1 passed lint, typecheck, build, and 36 frontend tests.
All inputs and identities were synthetic. No Google OAuth, cloud resource,
upstream extraction, private inbox, or real private document was used. The
source gate stays off; extraction, confirmation, and review UI are out of scope.
