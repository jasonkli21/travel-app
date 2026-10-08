# Phase 9 — Operational hardening

**Status:** Local hardening slice implemented; Phase 9 exit gate remains open
**Date:** 2026-10-05
**Migration:** `0015_provider_quota_buckets`
**Decision:** [ADR 0016](../decisions/0016-phase9-operational-hardening.md)
**Runbook:** [Local operations](../runbooks/phase-9-operations.md)

## Independent review remediation — 2026-10-06

The first independent review found concrete bypasses in quota path matching,
routing call reservation, private cleanup admission, backup verification, restore
validation, and proxy error headers. The local code now:

- recognizes canonical and compact UUID spellings consistently in backend
  admission/body guards and the web proxy;
- rejects route projections requiring more than the six calls reserved before
  provider work;
- admits upstream deletion immediately before each actual outbound cleanup
  request. Local confirmation, replay, and source deletion remain available when
  quota storage is exhausted; cleanup stays durable and pending for retry;
- creates a genuinely passwordless libpq connection URI and verifies the whole
  age-authenticated SQL stream in a protected temporary file before listing it;
- validates the restored `itinerary_proposals` table and forwards `Retry-After`
  through the web proxy;
- bounds ordinary JSON body acquisition to 60 seconds and cancels proxy body
  reads when its request deadline aborts.

Focused verification on 2026-10-06: full backend Ruff, formatting, and mypy
passed; targeted backend tests reported **25 passed, 18 skipped** (the skipped
booking-import and PostgreSQL cases require `TEST_DATABASE_URL`); the full
frontend Node test suite reported **53 passed**. Frontend package-manager
lint/typecheck could not run because pnpm attempted to fetch its package from
the unavailable npm registry.
These checks do not establish encrypted age/pg_dump recovery, real restore
publication, full-body deadline behavior under a live slow client, or multi-
instance quotas. Full Phase 9 remains open.

The review's owner-wide deletion workflow, full auth/browser route audit,
performance/concurrency and safe telemetry evidence, reproducible pinned images
and security scans, and production-container integration smoke are not supplied
by this remediation. RPO/RTO/retention approval, real SQL/blob restore, live TLS
proxy/service audiences/least-privilege roles, and separately authorized hosted
smoke remain external gates. Do not treat this remediation as Phase 9 closure.

## Delivered locally

- Added atomic PostgreSQL/SQLite fixed-window counters for owner and
  provider-wide admission. Provider-enabled research, comparison, proposal,
  extraction, and Geoapify search/route are admitted before provider work;
  upstream deletion is admitted per actual outbound attempt, leaving local
  confirmation and privacy deletion available. A missing shared counter store
  fails closed for provider calls.
- Added configurable per-provider shared ceilings and documented per-owner
  operation units. Logs report safe admission outcomes without owner identity,
  payload, source metadata, credentials, or provider URLs. These are request
  unit limits, not dollar caps.
- Added explicit `local`/`hosted` deployment mode. Hosted API configuration
  requires verified Google OIDC and exact HTTPS origins/hosts; Cloud Run cannot
  start in local identity mode. Production web proxy defaults to hosted mode
  and requires the session cookie for private API calls.
- Bounded database pool size/overflow/recycle settings. Defaults are five
  connections and zero overflow per API instance.
- Added age-encrypted SQL/private-store backup, hash inventory, verification,
  and isolated restore tooling, plus bounded cleanup for expired sessions,
  OAuth attempts, and quota rows.
- Hardened API/web production images to run non-root, minimized Docker build
  contexts, and added CI production-container startup/identity smoke checks.
- Added weekly Dependabot groups for Python, frontend, base images, and GitHub
  Actions; CI runs a high/critical production npm advisory check.
- Recorded operational choices, open recovery objectives, and local runbook.

## Verification and limitations

Local checks performed:

- Backend Ruff check/format check passed; mypy passed for `src` and `scripts`
  (103 files); operator scripts passed Python bytecode compilation.
- Frontend ESLint and TypeScript `--noEmit` passed; the Next.js production
  build passed.
- Alembic graph inspection reports `0015` as the only head. Full offline SQL
  generation stops at migration `0005`, which intentionally requires online
  data inspection/repair.
- Phase 9 tests were added but not run in this turn. The configured CI workflow
  runs PostgreSQL-backed tests, frontend tests, high/critical production npm
  audit, and API/web container startup smokes.

The local host has no Docker daemon, age executable, or disposable PostgreSQL
test database, so container startup and encrypted SQL/blob backup/restore were
not exercised here. A CI image smoke is not hosted identity, network, storage,
provider-billing, or deployment evidence.

RPO/RTO, backup cadence, and retention remain unapproved. The proposed values
are not reliability commitments. No backup/restore drill was performed against
a provisioned PostgreSQL environment; no Cloud Run, Neon, GCS, IAM role, or
provider billing control was created or exercised. Image base tags are not
registry-digest pinned, and CI build image IDs do not establish deployment
digests.

## Post-review Phase 3 readiness inventory

The existing CI baseline listed above remains in place. The post-review work
adds reproducible repository checks where local evidence is meaningful and
keeps production acceptance separate.

| Readiness item | Classification | Status and proof boundary |
| --- | --- | --- |
| Locked Python dependency advisory scan | CI-automatable | Added to CI. The local locked graph scan reported no known vulnerabilities on 2026-10-08. |
| Repository secret scan | CI-automatable | Added to CI. Tracked files and new scripts/tests pass against the reviewed baseline. |
| Synthetic encrypted SQL/private-store backup and restore, including restored application invariants and SQL-to-blob references | CI-automatable | Added to CI and passed locally with separate disposable PostgreSQL databases and an ephemeral age identity. Does not establish real RPO/RTO or independent recovery. |
| Bounded API behavior when the database pool is exhausted, then released | CI-automatable | Added PostgreSQL test requires a bounded 503 and a successful subsequent read; full backend suite passed. |
| 366-day trip, owner-place portfolio, request/query/export and memory evidence | Locally executable manual drill | Added and locally ran bounded `backend/scripts/benchmark_large_trip.py`; results are recorded below, with no flaky latency threshold. It does not prove concurrent or hosted load. |
| Complete auth/privacy and resource-bound route matrix, including multi-owner IDs, stale sessions, CSRF/logout, exports, owner migration, hostile input, and bounded request/output behavior | CI-automatable | Existing focused tests cover important cases; the full route matrix remains open. |
| Browser security-header and download/map behavior review | Locally executable manual drill | A local synthetic-data browser checklist is in the operations runbook; this observation drill remains open. Hosted TLS/proxy behavior is separate. |
| Base-image digest pinning, image/runtime vulnerability scanning, graceful shutdown under in-flight requests, and immutable deployable digest evidence | CI-automatable | Open. Dependabot update discovery and container startup smokes do not prove image vulnerability status, graceful shutdown, or deployment digests. |
| Owner-wide deletion inventory/workflow and restore-time deletion fence, with synthetic recovery regression coverage | CI-automatable | Open engineering work. No owner-wide deletion workflow or restore-time fence exists; implementation and synthetic tests must show that restoring an older snapshot cannot silently reintroduce deleted owner data. |
| Approved deletion disposition/retention, upstream AI-record outcomes, and real older-backup reconciliation | Production/operator-only | Open. Requires owner-approved policy and an operator drill against actual recovery points; point-in-time restore can reintroduce later-deleted data until the software fence and operational procedure are in place. |
| Approved RPO/RTO, backup cadence/retention, connection/capacity budget, real SQL/blob restore, and independent recovery environment | Production/operator-only | Open. Requires owner approval, real recovery points, measured elapsed time, and a separate recovery environment. |
| Cloud Run, IAM/least privilege, OAuth, TLS/proxy, service audiences, Neon pooling/load, provider credentials and hard billing ceilings, tile-key restrictions, secret rotation, and rollout/rollback | Production/operator-only | Open. Must be exercised by an authorized operator in the actual hosted environment. Local CI cannot establish these facts. |
| Multi-instance concurrency, provider cancellation/unknown outcomes, and production dependency/claim/storage telemetry | Production/operator-only | Local shared-PostgreSQL quota and aggregate-write concurrency tests exist, and pool bounds are tested; hosted load and production operational signals remain unproved. |

### Added repository-local gates and drill path

- CI now runs the locked Python advisory scan and tracked-file secret scan.
- The encrypted recovery smoke restores synthetic trips, reservations,
  proposals, booking-import outcomes, and both booking-source and trip-attachment
  bytes. Its review follow-up also seeds a partially written pending `.tmp`
  object and a complete pending promotion hard-link pair, checks both remain
  non-ready after restore, and replays both safely. It asserts restored SQL
  relationships, byte hashes, migration/ORM parity, and application JSON
  export behavior.
- Recovery validation rejects missing or mismatched bytes for a `ready` SQL
  attachment. A pending final object must match SQL; an interrupted pending
  `.tmp` file is snapshotted by its actual hash and remains pending until replay
  validates and promotes the complete upload. Present `deleting` bytes must
  still match SQL; absent pending/deleting objects remain recoverable.
- A pool-exhaustion regression test checks a bounded database-unavailable
  response and recovery after releasing the connection.
- Operators can run the bounded 366-day trip/500-place benchmark from the
  [operations runbook](../runbooks/phase-9-operations.md). Record the JSON
  evidence with host, database version, and revision before setting targets.
- The same runbook now includes a local browser checklist for observing
  document, export, attachment-download, and map responses with synthetic data.

Do not mark Phase 9 production accepted from these repository checks. Hosted
private data remains disabled until durable shared storage exists and all
production/operator gates are authorized and verified. A backup is a
point-in-time snapshot; this work does not prevent an older restore from
reintroducing data deleted after that snapshot.

### Phase 3 validation evidence — 2026-10-08

The repository-local checks were run on a local Apple Silicon macOS host with
PostgreSQL 16.15. The disposable databases and generated recovery identity used
synthetic data only.

- Backend: `uv run --locked pytest -q` with `TEST_DATABASE_URL` and
  `DATABASE_URL` pointed at the disposable PostgreSQL database — **295 passed,
  0 skipped**. Ruff check and format, mypy for `src`, package build, and Python
  bytecode compilation passed. Alembic upgraded to head, `alembic check`
  reported no operations, downgraded to base, and upgraded to head again.
- Security: locked uv export plus `pip-audit --require-hashes --disable-pip`
  reported **no known vulnerabilities**. `detect-secrets-hook` passed for
  tracked files and the new scripts/tests.
- Recovery: encrypted backup verified and restored at revision `0015`; the
  restored database contained one trip, reservation, proposal, and booking
  import, with two ready private objects, two matching SQL references, zero
  unreferenced objects, and a successful application JSON export.
- Large-trip drill: 366 trip days/items, 500 owner places, five iterations per
  request, and a two-connection pool with zero overflow. Trip detail measured
  111.48 ms median / 179.05 ms p95, six SQL statements, 270,674 response bytes,
  and 4.63 MB peak traced Python allocation. Owner-place reads measured 23.92
  ms median / 102.51 ms p95, one statement, 173,501 bytes, and 2.04 MB peak.
  JSON export measured 1,121.52 ms median / 1,163.72 ms p95, 15 statements,
  264,541 bytes, and 3.65 MB peak. Pool connections checked out after requests:
  zero. These are local observations, not production targets.
- Frontend package scripts passed using the existing dependency tree and the
  bundled Node v24.19 runtime: 54 Node tests, ESLint, Next route type generation,
  TypeScript checking, and production build. The bundled pnpm launcher tried
  to retrieve the repository's pinned package-manager version from npm, which
  was unavailable locally; frozen install and package-manager audit remain
  covered by the unchanged CI job.

The container job was not changed. No hosted environment, image registry
scanner, operator recovery environment, or production acceptance gate was
exercised by this validation.

### Post-implementation review fixes — 2026-10-08

This follow-up implements the review handoff findings TRV-010 through TRV-015:

- `TRAVEL_DEPLOYMENT_MODE` now binds from process and dotenv settings. The
  unprefixed `DEPLOYMENT_MODE` remains an explicit compatibility alias, with
  the documented name taking precedence; Cloud Run still rejects local mode.
- CI now uses the fixture-approved `127.0.0.1` and `travel_test` database. The
  mounted identity fixture parses the URL fields and rejects non-loopback,
  non-test, non-PostgreSQL, and unexpected-port destinations.
- Item and place forms retain the revision present when each draft opens. A
  refreshed conflicting revision preserves the entered draft, blocks its
  submit handler, and asks the user to close and reopen before using current
  values.
- Backup inventory normalizes the same-inode `<key>` / `<key>.tmp` promotion
  window while still rejecting external or unrelated hard links. Pending
  partial `.tmp` bytes are snapshotted by their actual digest and remain
  pending until same-key replay verifies the complete payload. The encrypted
  recovery smoke now covers both interruptions alongside ready objects.
- The readiness inventory separates owner-deletion and restore-fence
  implementation/CI work from policy approval and real operator recovery.
- The reviewed secret baseline was refreshed for metadata/filter and line
  movement only. The hook passed twice unchanged, and an injected synthetic
  credential was still rejected.

Checks on this fixes tree:

- Backend focused configuration and backup tests passed; the no-database full
  run reported **182 passed, 124 skipped**. Four tests that bind local HTTP
  sockets were blocked by this workspace sandbox; the remaining suite passed
  when those four were deselected. Ruff check/format and mypy for `src` and the
  two changed operator scripts passed.
- Frontend reported **58 tests passed**, including stale-draft interaction,
  uncertain-write recovery, explicit reload, and committed-create refresh
  failure coverage. ESLint passed, Next route type
  generation and TypeScript checking passed, and the webpack production build
  passed. The pinned pnpm install/audit sequence could not run because the
  package-manager fetch was unavailable. The new interaction checks invoke
  real component callbacks through a small hook harness; browser-mounted
  two-tab observation remains open.
- Markdown link validation passed for **220 links across 72 files**. The
  tracked-file secret hook passed twice with an unchanged final baseline.

The current fixes tree did not rerun the mounted PostgreSQL callback tests or
the encrypted backup/restore smoke: no local PostgreSQL service or `age` binary
is available. Container checks also remain unrun because Docker is unavailable.
No successful final CI run is recorded here. Phase 9 remains unaccepted until
the full CI jobs, real recovery path, and external gates are exercised.
