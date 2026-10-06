# Phase 9 — Operational hardening

**Status:** Local hardening slice implemented; Phase 9 exit gate remains open
**Date:** 2026-10-05
**Migration:** `0015_provider_quota_buckets`
**Decision:** [ADR 0016](../decisions/0016-phase9-operational-hardening.md)
**Runbook:** [Local operations](../runbooks/phase-9-operations.md)

## Delivered locally

- Added atomic PostgreSQL/SQLite fixed-window counters for owner and
  provider-wide admission. Provider-enabled research, comparison, proposal,
  extraction, Geoapify search/route, and upstream source-deletion requests are
  admitted before route/provider work; a missing shared counter store fails
  closed.
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
