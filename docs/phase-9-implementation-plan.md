# Phase 9 implementation plan — operational hardening and controlled deployment

**Status:** planned; no Phase 9 implementation/deployment delivered
**Date:** 2026-10-03
**Baseline:** reviewed Phase 0–4 commit `56c0cbf`, followed by accepted Phases 5–8
**Roadmap:** [phased implementation plan](09-implementation-plan.md)
**References:** [cloud direction](08-cloud-deployment.md),
[local development](07-local-development.md), [audit](reviews/phase-0-4-audit.md)

## Goal and scope boundary

Make the delivered personal travel application recoverable, observable,
securely deployable and economical to operate. Prove hosted behavior with
synthetic data before admitting private travel data. Keep local PostgreSQL
and private local attachments fully supported.

Deliver an authentication/authorization review of Phase 6, resource/cost
safeguards, safe operational telemetry, backup/restore/export/delete runbooks,
release/migration controls, container hardening and a separately authorized
GCP Cloud Run + Neon deployment smoke path. Operate accepted GCS only if
Phase 8 introduced it.

Phase 9 does not introduce authentication for the first time: private imports
were blocked until Phase 6 established it. Basic host/origin/body guards,
SQL readiness/timeouts and local migration recovery already exist in Phases
0–4 and must be preserved.

Defer multi-region/high-availability promises, Aurora DSQL migration,
dual-cloud operation, Kubernetes, service decomposition, collaboration,
automatic mailbox monitoring, full offline sync and a general observability/
workflow platform. No live account/resource provisioning or publication occurs
merely because this plan exists.

## Deployment gates and decisions

| Concern | Decision/gate |
| --- | --- |
| Identity | Audit verified owner mapping, web session/CSRF/logout, API ingress and travel-to-AI service identity. Local mode cannot be enabled on public ingress. Fail closed on invalid identity; default local owner is never a hosted fallback. |
| Exposure | Web is authenticated for private data; API is private/authenticated with explicit permitted callers. Set explicit hosts/origins and narrowly trusted proxy/TLS forwarding; do not accept arbitrary forwarded identity/host headers. |
| Compute/database | Follow accepted Cloud Run + Neon direction. Recheck current region, quota, pricing, pooling and TLS guidance at implementation. This is an operational choice, not a new domain dependency. |
| Containers | Immutable locked builds, non-root users, minimal build context/runtime, no baked server credentials, explicit bind/port, appropriate health/readiness and graceful termination. Public tile key is build-time configuration with restricted origin/API scope. |
| Database pool | Size pool/max overflow/instances/concurrency together against the actual database limit. Verify pooled-vs-direct DSNs, connection lifetime, statement/lock timeouts and migrations using the correct administrative path. |
| Requests/providers | Per-owner concurrency/rate controls, bounded payload/response/work time and explicit provider cost ceilings. Admission happens before provider work; request budgets account for SQL snapshot plus HTTP plus rendering, not just per-I/O waits. |
| Migrations | One authorized online migration job per release with backup and revision check; no per-instance startup migration. Use expand/contract for live compatible changes and record data-repair downgrade limitations. |
| Recovery | Define and approve actual RPO/RTO for personal data before claiming reliability. Test restore of SQL plus private blob metadata/bytes and migration version, not merely successful backup creation. |
| Telemetry | Request IDs, route templates, status/duration, dependency failure class, queue/claim age if present, storage readiness and quota consumption. Do not log private labels, documents, confirmation codes, tokens, SQL parameters or provider URLs. |
| Retention/deletion | One policy spans SQL, proposals/imports, blobs, exports and upstream AI records. Backups have a documented retention/expiry policy; deletion cannot silently claim to remove copies still retained elsewhere. |

Accept an operations/security ADR and deployment runbook specifying identity,
network topology, budget/quotas, secrets, database pool, retention, recovery
objectives and which external checks are mandatory. Recheck provider rights
and current service terms before real hosted use.

## Security and authorization review

Test every route and blob read/write against authenticated request-derived
owners, including provider/research, proposal detail/apply, import/source,
attachments and exports. Use multiple owners in tests even if deployment is
single-user allowlisted. Confirm owner migration cannot claim another person's
records and stale tokens cannot fall back to local.

Review CSRF and same-origin behavior behind the actual TLS proxy, trusted
forwarding, session expiry/rotation/logout, signed object URLs if used and
service-token audiences. Restrict open redirects, SSRF opportunities, document
remote fetches and browser active content. Content Security Policy and browser
headers must be validated against maps/downloads rather than blindly disabling
working features.

Verify least-privilege database, bucket, Secret Manager and service-account
roles; secrets are mounted/injected at runtime and rotated through a tested
runbook. Lockfiles and reproducible build artifacts remain authoritative.
Automate dependency advisory and secret scans with an actionable update policy.
No custom cryptographic verifier or broad owner header trust is acceptable.

## Operability and cost design

Expose liveness independent of providers and database readiness separately.
Dependency unavailability must not take unrelated manual features down.
Track DB pool acquisition/lock/statement failures and external deadline/
invalid-contract failures without private exception bodies.

Validate request-owned cancellation and unknown outcome recovery. Stop
automatic new-key retries; proposal/import replay reads their stored outcome.
Manual writes whose responses are lost require reload/reconciliation. If
Phase 6/7 accepted durable work, monitor stuck claims and bounded cleanup
rather than pretending an elapsed lease proves remote cancellation.

Set endpoint-specific rate/concurrency controls and provider admission
budgets. Multi-instance controls must coordinate: an in-memory limiter alone
cannot enforce a shared owner/provider quota. Use the simplest concrete
shared mechanism that fits actual traffic (for example a small SQL quota/claim
record or managed edge controls), with documented fail-closed/cost behavior.
Do not introduce Redis solely because it is common.

Recheck billing/credit models for Geoapify, AI/search/model/storage/database.
Use conservative max instances/concurrency, provider spend limits where
available, budget alerts and tested exhausted-quota states. Alerts are not a
hard spending cap; document which controls actually stop work. Provider
unavailability/quota denial leaves manual CRUD usable.

Browser map tiles bypass the API admission path. Restrict the public tile key
by origin/API and use tested provider-side ceilings where available; record
tile billing/alert limits explicitly. A server-side research/routing limiter
does not by itself cap direct tile spend.

Measure representative personal trips plus the documented 366-day limit and
larger owner place portfolios. Establish measured p95 manual-read/write targets,
SQL query counts, pool bounds, response limits, export time and memory ceiling
before optimizing. Add pagination/virtualization only where the measurement
shows need, with a versioned compatibility plan.

## Backup, restore, export and deletion

Provide encrypted/access-controlled SQL backups and consistent private object
inventory/hash metadata. Choose backup cadence/retention against approved
RPO/RTO and validate provider facilities rather than assuming a free plan
includes sufficient recovery.

A restore drill recreates a separate environment from a backup, checks Alembic
revision/ORM parity, verifies representative trips/reservations/proposals/import
outcomes, resolves blob hashes/references and runs read/write smoke tests with
synthetic data. Record elapsed time and achieved recovery point. Never restore
over a live environment without explicit operator authorization.

Versioned Phase 8 JSON exports support owner access to data. If a transfer/
restore tool is built, require schema version validation, owner remapping,
dry run, duplicate/conflict report, FK/time/order checks and one bounded
transaction per accepted unit. Do not treat arbitrary exported JSON as SQL or
an automatic merge instruction.

Deletion tooling enumerates authoritative SQL, source/attachment bytes,
temporary objects, retained proposal/import data and external AI references.
Coordinate cleanup without dropping the last object reference; preserve safe
tombstone/deletion state until retries finish. Report backup/upstream retention
limits accurately. Export/delete tools require owner authorization, explicit
destructive confirmation and tests for another owner's isolation.

## Release, migration and rollout design

CI must execute all migrated PostgreSQL suites without skips, backend quality/
package checks, frontend tests/types/build and the relevant identity/storage/
export/security checks. Build images from locked dependencies, record image
digests and migration revision, and scan runtime images/dependencies. Test
production standalone web/API startup, non-root file permissions and graceful
shutdown under in-flight requests.

Use an operator-visible preflight:

1. Confirm approved environment/identity/budget and synthetic-data policy.
2. Verify secrets, private API topology, TLS/database connectivity/pool and
   host/origin/forwarded-protocol configuration.
3. Back up and verify the current revision; run a single online migration job.
4. Roll out immutable image digests with compatible schema/API versions.
5. Smoke authenticated manual CRUD, provider-disabled behavior, proposal/
   import replay, private downloads/exports and quota-denied states.
6. Exercise rollback/recovery strategy and document when data repair requires
   restoration rather than Alembic downgrade.

Migration 0005 is online-only and changes data irreversibly without a backup.
Later repairs must make similar limits explicit. Do not run migration from
every server startup. If an incompatible change is necessary, use a controlled
maintenance window or expand/contract rollout; document which old images remain
safe against the new schema.

Keep local setup independent of cloud credentials and preserve an isolated
local smoke environment. Authentication/provider gates must be explicit rather
than relying on absent secrets to disable sensitive functionality.

## Dependency map and work packages

~~~text
P9.0 operational objectives + topology/security/recovery ADR
          |
P9.1 auth/privacy/resource audit + safeguards
          |
P9.2 safe telemetry + quotas/performance bounds
          |
P9.3 backup/restore/export/delete drills
          |
P9.4 locked container/release/migration controls
          |
P9.5 separately authorized hosted smoke + runbook/release
~~~

### P9.0 — Accept operational objectives

Record RPO/RTO, retention, approved environment/topology, pool budget, actual
provider ceilings and deployment gates.

**Acceptance:** every claimed reliability/security/cost property has a
measurable test or explicit external prerequisite.

### P9.1 — Audit and harden identity/privacy/resources

Exercise all owner boundaries, TLS/proxy/session/service-token behavior,
document/URL handling and least privilege. Close valid findings with tests.

**Acceptance:** no hosted local fallback, forged caller trust, cross-owner data
access or private payload logging remains; sensitive features fail closed.

### P9.2 — Add measured telemetry and quotas

Implement concrete safe metrics/log correlation, bounded provider admission
and shared multi-instance safeguards. Measure SQL/pool/memory/export behavior.

**Acceptance:** exhausted quotas and dependency failures are observable and
recoverable; limits apply before billable work and across instances.

### P9.3 — Prove recovery and deletion

Add backup inventory/runbooks, isolated SQL/blob restore drill, validated
owner export/delete tooling and idempotent cleanup.

**Acceptance:** restoration meets approved objectives and verifies data/blob
integrity; deletion isolation and retention limits are demonstrated.

### P9.4 — Make releases reproducible

Harden containers, CI gates, image digests, migration job and compatible
rollout/rollback procedure. Run local container integration smoke tests.

**Acceptance:** locked non-root production images run correctly, migrations do
not race at startup, and rollback limits are known before release.

### P9.5 — Execute approved hosted smoke and handoff

Only after separate authorization, configure private Cloud Run/Neon and
accepted storage, use synthetic data, exercise the runbook and record exact
versions, checks, remaining gaps and operating commands.

**Acceptance:** hosted identity/network/storage/provider gates are proven, not
inferred from local builds. Admit private data only after the release checklist.

## Verification matrix

| Area | Required evidence |
| --- | --- |
| Identity/security | Multi-owner route/blob tests, forged/expired tokens, CSRF/logout, actual TLS proxy origins/forwarding, service audiences, no local fallback, least privilege |
| Resource/privacy | Request/upload/output/deadline bounds, hostile documents/URLs, no sensitive logs/traces/metrics, advisory/secret/image scans and rotation smoke |
| Concurrency/limits | Pool exhaustion/lock waits, aggregate revision/apply/import replay, multiple-instance quotas, provider budget denial, graceful cancellation/unknown outcomes |
| Recovery | Separate-environment SQL/blob restore, hashes/FKs/revisions and representative workflows, measured RPO/RTO, cleanup/deletion isolation and retention disclosure |
| Release | All SQL suites execute, package/frontend/container checks, immutable digests, one online migration job, compatibility/rollback and production smoke |
| Hosted | Authorized synthetic Cloud Run/Neon/storage path, private API ingress, host/origin/TLS/session correctness, readiness/dependency outage/quota-denied behavior |
| Local parity | No-cloud startup, local PostgreSQL/auth mode/blob configuration, fake providers, migrated tests and standalone/container smoke |

Use existing Make/check commands plus concrete security/restore/container
checks. Record exact commands, timestamps, revision/digests and environments.
An unavailable external gate prevents a hosted-ready claim; it does not erase
the useful local hardening work.

## Commit sequence and exit gate

1. docs: accept operating/security/recovery objectives and runbook.
2. fix: close identity/privacy/resource audit findings.
3. feat: add measured telemetry and provider admission safeguards.
4. feat: add recovery/delete tools and restore verification.
5. chore: harden locked containers and release/migration pipeline.
6. ops: record separately authorized hosted smoke and final release evidence.

Phase 9 completes only when approved recovery objectives, safe resource/cost
controls, security review and actual deployment gates have been verified.
A repository with deployment documents alone remains local-delivered.
Aurora DSQL or another cloud migration requires a new ADR and transaction/
retry proof; none of these tasks promises drop-in database portability.
