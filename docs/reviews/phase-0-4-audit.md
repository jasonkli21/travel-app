# Phase 0–4 architecture and implementation audit

**Date:** 2026-10-03
**Reviewed baseline:** da51ec63006570d6b931b2da3119f57f35203141
**Scope:** completed local Phases 0–4; remediation does not implement Phases 5–9.

## Conclusion and scope

The relational modular monolith, manual-first workflows, explicit provider
submission, and external typed AI boundary are appropriate for the delivered
product. The original plans missed important boundaries for local browser
security, timed moves, resource limits, recovery, and proving SQL behavior in
CI. Several correctly stated invariants also were not met by the implementation.

The review covered the product/design/architecture/data/AI/cloud documentation,
ADRs, phase plans and release records; all backend routes, schemas, models,
repositories, services and clients; migrations and fixtures; frontend routes,
forms, workspace/map/research state and HTTP proxy; dependency locks, packaging,
Docker, Compose, scripts, environment examples and CI. Historical release
records describe their original checks; this record supplies current evidence.

## Gaps in the plans and design

| Gap | Corrected requirement and disposition |
| --- | --- |
| CORS was effectively the only browser boundary around a configured local owner. CORS does not prevent a hostile page from submitting a write. | Explicit API/web host and origin checks, bounded request bodies and loopback development defaults are required now. They do not authenticate command-line clients or make public deployment safe. |
| Moving a timed item had no explicit date semantics. | Preserve local HH:MM on the destination day. Resolve both endpoints before changing the graph; reject DST gaps/folds atomically. Cross-midnight itinerary items remain unsupported; reservations may cross midnight. |
| Ordering and coordinate validity depended too heavily on service validation. | SQL enforces unique day/order, nonnegative order, paired coordinates and geographic ranges. Contiguity and owner/trip cross-record relationships remain service invariants, with real database concurrency tests. |
| Multi-query aggregate reads had no consistency rule; identity-map refresh behavior was unspecified. | Trip-root shared read locks and exclusive mutation locks protect the loaded itinerary/reservation graph under PostgreSQL. Refresh previously loaded ORM collections before mutation. Reusable place metadata is independently locked and is not a trip-version snapshot. |
| “Bounded timeout/stream” did not distinguish per-I/O timeout from elapsed deadline, or buffered JSON/lines from bounded reads. | Bound entire external operations, decoded body bytes, SSE line/frame/event/total sizes, route waypoint groups and eligible leg count. Release SQL transactions before provider awaits and execute synchronous SQL outside the event loop. |
| UI success/error requirements did not address a committed write followed by a failed refresh. | Close successful creation forms, preserve the last complete workspace, identify it as stale, block edits and offer reload. Guard same-turn double submission; hide late search/research output under changed context. |
| Test requirements allowed database suites to disappear behind skips. Fixtures were not required to match production sessions or real migrations. | CI supplies TEST_DATABASE_URL and executes every SQL suite against migrated disposable schemas with production session options. Offline-only verification must explicitly report skips. |
| Basic operability and recovery were postponed too broadly to Phase 9. | Provide liveness vs database readiness, request IDs, safe status/duration logs, database waits, migration parity and backup/restore guidance now. Hosted metrics, rate/cost quotas and restore drills remain Phase 9 work. |
| “Portable SQL” could imply Aurora DSQL compatibility without testing concurrency semantics. | PostgreSQL/Neon is the implemented target. A DSQL migration needs a separate transaction/retry design and integration proof, not a DATABASE_URL swap; see ADR 0009. |
| Authentication sequencing, proposal versions, document storage and extraction lifecycle were not concrete in the remaining roadmap. | Phase 5 must version manual mutations and shared place dependencies before applying proposals. Phase 6 must authenticate before private imports and introduce the minimal secure blob lifecycle; Phase 8 extends it. Phase 9 reviews and operates authentication rather than introducing it after sensitive ingestion. Detailed remaining plans follow the remediation commit. |

## Implementation findings and fixes

### Persistence, correctness and concurrency

1. Cross-day moves retained source-date timestamps. Conflicts, route gaps and
   subsequent timezone edits could be wrong. Moves now re-resolve local times
   on the destination date and roll back schedule/order together on failure.
2. Invalid timezone paths raised ValueError rather than a domain error.
   Zone lookup now handles invalid paths; datetime boundary overflow also fails
   safely instead of returning an unexpected server error.
3. Duplicate sort positions and invalid coordinates were possible through SQL.
   Migration 0005 adds constraints, repairs legacy moved-item dates and
   normalizes order deterministically. Two-stage temporary positions avoid
   immediate unique violations on reorder/move/delete; temporary positions are
   above both occupied and final positions. Deletes flush before compaction.
4. Production sessions keep objects after commit and disable autoflush. A trip
   loaded before locking could retain stale collections, while reservation or
   candidate creation left parent collections incomplete. Aggregate queries
   populate existing state and new children join their parent collections.
5. Aggregate reads could combine child queries across a concurrent mutation.
   Shared root locks protect those reads; exclusive root locks serialize writes.
   Place updates acquire their own row lock. Concurrent appends, opposite
   cross-day moves and cross-trip provider imports are tested.
6. Trip summary listing unnecessarily hydrated the entire graph. Counts now
   come from SQL with deterministic tie-breaking, preserving the public API.
7. Provider orchestration performed synchronous SQL inside async methods.
   Snapshot projection and rollback now run in the thread pool, with immutable
   values carried into external calls and no database lock held during them.

### Provider and AI boundary

8. JSON byte checks happened after HTTPX had buffered the response. Shared
   streamed readers enforce decoded-byte limits before assembling JSON.
   Geoapify responses are capped at 2 MB; research responses at 1 MB and health
   responses at 4 KiB.
9. SSE line iteration could buffer an arbitrarily long unterminated line.
   The byte parser bounds lines before UTF-8 decoding, supports CR/LF/CRLF,
   and preserves existing frame, event count and total limits.
10. Per-I/O timeouts allowed a trickling provider to exceed the web proxy
    deadline. Research create/run/detail shares one elapsed deadline, default
    45 seconds and configurable up to 50. Geoapify has an elapsed request
    deadline; a complete logistics estimate has a 30-second provider deadline.
11. Route requests could create excessive URLs and provider work. Estimates
    accept at most 50 eligible transfers and split consecutive routes into
    groups of at most ten waypoints, preserving joining transfers. Returned
    geometry is bounded to 10,000 points per route line.
12. Research accepted whitespace-only answers and could present a session past
    a citation's earlier expiry. Completed results require nonblank answers and
    valid citations; the result expires at the earliest session/citation expiry.
    Legitimate terminal-to-expired detail reconciliation is allowed.
13. Website/citation/source links needed one consistent safe URL rule. Shared
    validation rejects non-HTTP(S), credentials, controls, backslashes and
    malformed ports. Legacy arbitrary reservation references render as text.
14. Averaging longitude gave an incorrect search bias near the date line.
    A circular longitude mean now agrees with the map's wrapped world.

### HTTP, security, configuration and operability

15. Local endpoints accepted hostile browser origins and hosts. API middleware
    and the same-origin web proxy reject them before domain/provider work.
    Host checks validate authority syntax; the proxy uses the browser Host
    because Next can construct an internal-host request URL.
16. API/proxy JSON requests were unbounded. Both enforce 64 KiB even without
    Content-Length. Item/reservation notes are limited to 10,000 characters,
    candidate notes to 1,000; UI limits match the HTTP contracts.
17. Validation errors echoed private input and exception context. Responses
    now retain safe field locations/types/messages without input or exception
    objects. Database failures use a safe 503 envelope; unexpected failures
    use a safe 500 and correlate by request ID without propagating private
    exceptions into default server tracebacks. Responses are not cached.
18. Health did not distinguish process availability from usable storage.
    /health stays liveness; /ready checks SQL. Connection, pool, statement and
    lock waits are bounded. Request logs contain route templates, status and
    duration, excluding user content, identifiers, query strings and secrets.
    Only the application logger is enabled; supplied Uvicorn commands disable
    query-bearing access logs rather than enabling HTTP-client/root debug logs.
19. Compose exposed PostgreSQL on all interfaces and development web defaults
    could listen publicly. Both default to loopback. API Docker context now
    excludes local environments, caches and secrets. Public Geoapify tile
    configuration is explicitly a build-time Docker argument.

### Frontend correctness and maintainability

20. A write followed by failed refresh was treated as a failed write, inviting
    duplicate creation. Success and refresh failure are separated; the stale
    workspace is visibly blocked until reload.
21. Partial refreshes mixed old and new supporting records. The workspace
    publishes data only after all requested reads succeed and keeps its last
    complete state on failure. Separate HTTP calls are not a database snapshot
    across other tabs; Phase 5's versions must address stale manual/proposal
    edits rather than pretending the UI supplies cross-request isolation.
22. Fast repeated writes/quick-place/import/logistics actions could overlap
    before React rendered disabled controls. Synchronous refs guard mutations
    and provider submission. Search generations reject late old-query output.
23. Research remained visible after changing selected-day content, and timer
    expiry depended on an impure render-time clock. Context fingerprints hide
    old responses after edits, and clock state updates at expiry. Query/day/
    freshness changes clear the result. Removed map/research days fall back
    safely. Trip-ID keys prevent state reuse across trip navigation.
24. Manual coordinate entry was absent despite the documented map workflow.
    The place form now exposes paired numeric coordinates and clear validation.
25. Large inline forms and one schema file obscured workflow boundaries.
    Itinerary/day/reservation/place/candidate forms and HTTP schemas are grouped
    by workflow. Shared domain types/URL policy remove client-to-API coupling;
    unused repository protocols were removed. Frontend tests live under tests/
    rather than scripts/. Runtime API routes and ordinary CRUD contracts remain
    compatible.

### Testing and delivery integrity

26. CI started PostgreSQL but never set TEST_DATABASE_URL, skipping the SQL
    suites. It now executes them and the frontend Node tests.
27. Database fixtures dropped/recreated application tables using a supplied
    database and bypassed migrations. Shared fixtures create UUID-named schemas,
    apply actual Alembic migrations, check ORM parity, and remove only owned
    schemas. Migration tests use their own disposable schema.
28. Fixtures used different autoflush/expiration options from production, and
    dormant tests contained detached-object, implicit-transaction and undefined
    fixture-ID failures. Sessions now match production; those tests run and
    have been corrected.
29. Alembic could disable application logging and lacked a reusable connection
    seam for safe migrated test schemas. Logging stays enabled, supplied
    connections are supported, and path separator configuration is explicit.
30. Regression coverage now exercises SQL integrity, migration upgrade/repair/
    downgrade/rollback, destination DST moves, concurrency, safe boundaries,
    oversized/trickling upstream responses and context/link rules. The real
    production browser check found the internal-host proxy mismatch that a
   pure unit suite missed; that case now has a regression.
31. Transport/5xx failures were treated as definitely failed manual writes,
    although a commit may have succeeded before its response was lost. Trip,
    itinerary/reservation/place/candidate/import writes now block resubmission
    until reload/reconciliation and explicitly describe the unknown outcome.
    Trip creation also has a synchronous guard through navigation. Validation
    failures remain editable without that recovery step.

## Migration and compatibility

Migration 0005 is an online data repair plus constraints. Back up first and
stop application writes during migration. It rejects invalid existing place
coordinates and moved schedules that cannot be resolved without guessing.
The migration fails transactionally; correct the identified legacy data and
retry. Downgrade removes constraints but cannot reconstruct the old timestamps
or order; restoration requires the pre-migration backup. Instructions are in
[local development](../07-local-development.md).

Host/origin allowlists and field/body bounds intentionally reject formerly
accepted unsafe or excessive requests. Destination-day timestamp correction
is an intentional bug fix. Ordinary route paths, response fields and CRUD
behavior are preserved. Phase 4 still stores no AI sessions/evidence.

## Verification actually performed

| Check | Result |
| --- | --- |
| PostgreSQL 16.15 on isolated loopback cluster | Full backend suite: 86 passed, zero skipped; production session settings and real migrations |
| Backend quality | Ruff lint/format and strict mypy pass |
| Migrations | Clean head, ORM parity, downgrade to base/re-upgrade, legacy repair and DST-repair rollback covered |
| Python packaging | Offline uv build produces wheel and sdist |
| Frontend | ESLint, Next type generation, strict TypeScript and production standalone build pass |
| Frontend Node tests | 14 passed: map geometry, response handling, proxy security/body/error/request ID, internal-host origin, research context, safe links and actual HTTP-client uncertain mutation outcomes |
| Production smoke | API + standalone Next against a separate migrated database; same-origin creation, browser item creation/move, coordinate place/candidate save, reload persistence, disabled research error and controlled backend-outage/reload recovery |
| Dependency advisories | pip-audit reports no known vulnerabilities in installed Python dependencies; the unpublished local package is not auditable on PyPI. pnpm production audit reports zero advisories across 93 dependencies |

The Make targets' underlying installed executables were used because this
host's default uv cache/Corepack setup was unavailable. No lockfile update was
needed. The checks used no real Geoapify or AI credentials. Browser automation
could not reliably fill native date controls, so synthetic trip dates were
seeded through the production same-origin proxy; date/DST contracts were
verified by the PostgreSQL/API suites. One Starlette/httpx deprecation warning
remains, without a failed check.

## Known limits and deliberately deferred work

- Local configured ownership remains unauthenticated. Host/origin guards do not
  authorize native clients. Public or sensitive deployment requires verified
  identity, authenticated service boundaries and quotas.
- No live Geoapify tiles/search/routes, real upstream AI/search/storage,
  Docker-image execution, hosted CI run, cloud deployment or provider-rights
  approval was performed. Mocked client success/failure tests do not prove
  provider compatibility in a deployed environment.
- Full trip/owner place lists are still appropriate for personal scale; there
  is no arbitrary cap or breaking pagination contract. Large-portfolio
  pagination and UI virtualization need measured requirements.
- Manual cross-tab edits currently use last-writer semantics; aggregate locks
  protect invariants, not user edit intent. Phase 5 must add optimistic versions
  and proposal replay protection before enabling typed AI apply.
- A deadline/cancelled request can leave upstream research work completing
  independently. Travel must not retry automatically with a new key. Durable
  resumption/cancellation needs an accepted upstream contract and belongs in
  later bounded research/import planning.
- The UI has Node boundary/geometry regressions and documented browser smoke,
  not a full component/browser automation suite. Phase 5 should introduce
  focused stale-preview/apply and mutation-refresh failure UI coverage.
- No private uploads, extraction jobs, attachment access, proposal application,
  offline synchronization, cloud resources or authentication were added.
  Dependency audit results are time-specific, not a permanent guarantee.
