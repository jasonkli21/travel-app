# Phase 9 local operations runbook

**Status:** Local procedures; hosted topology and recovery objectives remain
unverified

This runbook covers the local PostgreSQL and private-store implementation. Do
not use it as proof of a hosted backup, provider billing cap, or cloud restore.
Do not restore over a live database. Cloud deployment and resource provisioning
require separate authorization.

## Recovery objectives

RPO, RTO, backup cadence, and retention are **TBD pending explicit approval**.
The proposed starting values in [ADR 0016](../decisions/0016-phase9-operational-hardening.md)
are not service commitments. Record the accepted values and measured drill
results here before making a reliability claim.

## Prerequisites

- PostgreSQL client tools `pg_dump` and `pg_restore` matching the server's
  supported major version.
- [`age`](https://github.com/FiloSottile/age) installed on the operator host.
- An age recipient for backup creation. Keep the matching private identity
  outside the repository, restrict its file permissions, and store a second
  protected copy separately from the backup destination.
- A private local store directory, including an empty directory when there
  are no private bytes to inventory.
- A database role permitted to dump/restore the required application schema.
  The restore role needs an empty destination database.
- A maintenance window in which all application/API writers and cleanup jobs
  are stopped. The backup command refuses to run without an explicit
  `--confirm-writes-stopped` acknowledgement.

## Create and verify an encrypted backup

From `backend/`, with the source database URL set in `DATABASE_URL`:

```bash
uv run python scripts/secure_backup.py backup \
  --store-dir /absolute/path/to/private-store \
  --output-dir /absolute/path/to/encrypted-backups \
  --recipient age1... \
  --confirm-writes-stopped
```

The command prints the artifact path, encrypted byte count, and SHA-256. Record
that output, the current Git revision, database revision, operator, and start/
finish time in the protected operations log. The artifact contains an encrypted
custom-format PostgreSQL dump, an encrypted tar of private-store bytes, and an
inventory with per-object size/hash and the Alembic revision. Coordination
`.lock` files are excluded; temporary and other regular source-store files are
included. Symbolic links and unsupported file types stop the backup.

Verify the exact artifact before relying on it:

```bash
uv run python scripts/secure_backup.py verify \
  --artifact /absolute/path/to/encrypted-backups/travel-....tar.age \
  --identity /secure/path/travel-age-identity.txt
```

Verification decrypts the outer archive, checks every private object against
the encrypted manifest, authenticates/decrypts the database archive, and asks
`pg_restore` to list its contents. A successful backup command alone does not
prove recoverability. Keep backups encrypted and access controlled; this tool
does not schedule them or prune old artifacts.

## Restore drill to an isolated destination

1. Choose or create a **separate, empty** PostgreSQL database. Do not point
   `RESTORE_DATABASE_URL` at a live database. Use a restore role with schema
   creation rights.
2. Choose a private-store destination path that does not exist.
3. Keep application writers stopped for the destination. Set
   `RESTORE_DATABASE_URL` in the process environment; do not put credentials on
   the command line.
4. Run from `backend/`:

   ```bash
   uv run python scripts/secure_backup.py restore \
     --artifact /absolute/path/to/encrypted-backups/travel-....tar.age \
     --identity /secure/path/travel-age-identity.txt \
     --store-dir /absolute/path/to/isolated-restored-private-store
   ```

   The command refuses a database containing application tables and refuses
   an existing private-store destination. It restores SQL without owner/ACL
   replay, verifies the Alembic revision and ORM parity, checks representative
   table counts, performs a temporary write/read probe, verifies every private
   object hash, and publishes the private directory only after the SQL checks
   pass. If SQL restore fails, keep the destination isolated for inspection or
   discard that **new** database and retry; the command never cleans it for you.
5. Record achieved recovery point, total elapsed time, restored Alembic
   revision, trip/reservation/proposal/import counts, private-object count,
   missing objects, and any manual recovery steps. Compare results to the
   approved RPO/RTO before closing the drill.
6. Run an application smoke check against only the isolated database/store,
   then destroy the drill environment through its normal local database
   tooling. Never copy restored private bytes into a live store as a shortcut.

The tool writes temporary encrypted archives and extracted restore material
under a mode-0700 temporary directory, removes it on exit, and uses a temporary
mode-0600 PostgreSQL passfile rather than putting passwords in process
arguments. A forced process termination can leave temporary files; operators
must inspect and remove only their own `travel-backup-*`, `travel-verify-*`,
and `travel-restore-*` temporary directories after checking no process is using
them.

## Bounded operational cleanup

Expired sessions/OAuth attempts and provider quota windows are cleaned in
bounded batches. First inspect the proposed pass:

```bash
uv run python scripts/cleanup_operational_state.py --limit 500
```

After reviewing the counts, execute that bounded pass:

```bash
uv run python scripts/cleanup_operational_state.py --limit 500 --execute
```

Quota counters are retained at least one day past their last possible active
window. This command does not delete deletion intents, owner migration audits,
trip data, exports, or attachment tombstones.

The existing private-source reconciler is also bounded and rerunnable. Continue
from each returned cursor until both report `complete`:

```bash
uv run python -m personal_travel.services.source_cleanup --limit 100 --seconds 5
```

Use `--owner-id` only for an operator-selected owner scope. This reconciles
local source bytes and metadata; it does not claim that upstream AI deletion
has completed. Inspect and retry durable upstream deletion intents through
the app's authenticated cleanup path. Preserve tombstones until deletion is
confirmed or the documented retention/disposition is recorded.

## Quota and telemetry response

Provider admission returns `429` with `Retry-After` when an owner or shared
provider budget is exhausted. It returns `503` when the shared database-backed
counter cannot safely admit a request. Logs contain request ID, operation,
provider, charged units, outcome, and retry interval. They do not contain owner
IDs, request payloads, document labels/bytes, credentials, or provider URLs.
Use the request ID and route-template request log to correlate a failure.

These provider-unit budgets do not guarantee a price ceiling. They also do not
limit the browser's direct map-tile requests. Configure and verify provider
billing ceilings/alerts and origin-restricted tile keys before any live use.
Manual trip CRUD should remain usable during provider denial or outage.

## Release preflight and stop conditions

- Confirm the environment, verified identity, synthetic-data policy, host and
  origin allowlists, TLS/proxy behavior, secret versions, and approved provider
  budgets.
- Verify database TLS/connectivity and that
  `instances × (pool_size + max_overflow)` fits the database connection budget.
  Current per-instance defaults are 5 and 0; no hosted instance cap is set.
- Create and verify a backup. Run one separately authorized online migration
  job, check the expected Alembic revision, and keep migration out of web/API
  startup.
- Roll out only reviewed immutable image digests. Smoke authenticated manual
  CRUD, provider-disabled paths, quota denial, private downloads/exports, and
  proposal/import replay with synthetic data.
- Exercise the selected rollback path. Migration `0005` is online-only and
  needs a pre-upgrade backup; a downgrade is not a substitute for restoring
  repaired data.
- Stop before hosted deployment until Cloud Run/Neon/IAM/network/secrets,
  provider spend controls, and backup storage/retention are explicitly
  authorized and verified.
