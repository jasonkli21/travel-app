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

Phase 9 remains open for approved recovery objectives, measured SQL/blob
restore and deletion-isolation drills, owner-wide deletion inventory and
upstream AI-record disposition, enabled repository vulnerability and secret
scanning, base-image digest pinning, measured performance and pool exhaustion,
public tile spend controls, and the separately authorized hosted smoke path.
Do not admit private hosted data until the release checklist is complete.
