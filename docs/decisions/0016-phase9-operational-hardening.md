# ADR 0016 — Phase 9 operational hardening

**Status:** Accepted for local implementation; recovery objectives and hosted
operation remain open
**Date:** 2026-10-05

## Context

The app now includes verified identity and private local attachment lifecycles,
but its Phase 8 release does not claim whole-phase recovery or hosted readiness.
Phase 9 needs shared provider limits, fail-closed hosted identity settings,
reproducible non-root containers, and a recoverable SQL/private-byte snapshot.
Local PostgreSQL and local private storage remain supported. Cloud Run, Neon,
GCS, provider credentials, and live deployment are not provisioned by this
decision.

## Decisions

1. Provider admission uses atomic fixed-window counters in PostgreSQL, keyed by
   a one-way owner hash and operation, plus a provider-wide counter. Every
   configured instance uses the same database rows. Admission runs after
   request identity/CSRF checks and before billable provider work. For durable
   private deletion, admission occurs per actual outbound attempt after the
   intent is loaded, so local confirmation, replay, source deletion, and empty
   cleanup retries do not depend on provider quota availability. Failure to
   read or update the shared counters prevents the provider call; synchronous
   provider operations return `503` and exhausted budgets return `429` with
   `Retry-After`.
2. A quota unit is a conservative provider request unit, not a monetary amount.
   Route estimates reserve six Geoapify units and reject a projection that
   would require more than six HTTP batches, even when it has fewer than 50
   eligible legs. Current defaults are:

   | Provider / operation | Per owner per minute | Per owner per day | Shared provider per minute | Shared provider per day |
   | --- | ---: | ---: | ---: | ---: |
   | Geoapify search | 8 | 100 | 30 units | 200 units |
   | Geoapify route estimate | 12 units | 60 units | same Geoapify pool | same Geoapify pool |
   | Personal AI research | 4 | 40 | 12 units | 100 units |
   | Personal AI comparison | 3 | 20 | same AI pool | same AI pool |
   | Personal AI proposal | 2 | 10 | same AI pool | same AI pool |
   | Personal AI extraction | 1 | 5 | same AI pool | same AI pool |
   | Personal AI deletion | 2 | 20 | same AI pool | same AI pool |

   Operators can lower or raise shared ceilings through settings within the
   validated ranges. The shared database ceiling applies across owners and
   instances. It is not a dollar cap. Provider billing controls, alerts, and
   the direct browser map-tile key require separate provider configuration.
   Deletion cleanup charges one unit per outbound attempt, up to its bounded
   ten-intent pass; empty batches charge nothing.
3. `TRAVEL_DEPLOYMENT_MODE=hosted` requires Google OIDC, explicit non-loopback
   API hosts, and explicit HTTPS browser origins. Cloud Run's `K_SERVICE`
   marker makes local mode fail during settings validation. Production web
   builds default to hosted mode and require a session cookie for private API
   proxy requests. Local development remains explicit and first-class.
4. Each API instance defaults to five database connections with zero overflow
   and a 30-minute recycle. This is a per-instance cap, not a global Neon
   connection budget. Any future hosted max-instance/concurrency setting must
   be checked against the database's actual connection limit.
5. Production containers run as non-root. Startup does not perform migrations;
   migration remains a separate, backed-up operator action.
6. SQL and local private bytes are backed up together only while every app
   writer is stopped. The backup tool encrypts the database dump and complete
   archive with age, inventories private bytes by size and SHA-256, and only
   restores to a separate empty database and a new private directory.
7. Dependabot weekly groups cover uv, npm, both Dockerfiles, and GitHub Actions.
   CI rejects high/critical production npm advisories. Enabling GitHub
   dependency-graph alerts/security updates and secret scanning remains a
   repository-security setting to check separately.

## Open operational objectives and gates

No RPO, RTO, backup cadence, or retention period is accepted yet. The proposed
starting point is RPO at most 24 hours, RTO at most four hours, encrypted daily
backups retained seven days, and encrypted weekly backups retained four weeks.
These values must be explicitly approved and then measured in a restore drill;
the app does not schedule or expire backups automatically.

The target topology remains the previously documented private API / web
frontend / managed PostgreSQL direction. No IAM roles, network policy, secrets,
storage bucket, provider spend cap, or service instance cap is established
here. Hosted smoke, provider billing, key restrictions, secret rotation,
least-privilege roles, and the restore drill against the selected hosted
database/storage remain release gates.

## Consequences

- Provider outages, quota denial, or quota-database failure do not become
  silent provider calls; manual trip operations remain outside those budgets.
- PostgreSQL stores pseudonymous owner hashes and short-lived quota counters.
- Backup operation requires local `pg_dump`, `pg_restore`, `age`, and a
  protected age identity. Operators must quiesce all writers themselves.
- Backups are not per-owner deletable. Deletion reports must disclose copies
  still within the approved backup retention window and upstream deletion
  outcomes.
- This ADR establishes local safeguards. It does not claim Phase 9 complete or
  authorize provisioning/deployment.
