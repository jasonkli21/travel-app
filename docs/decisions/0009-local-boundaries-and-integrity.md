# ADR 0009 — Phase 0–4 local boundaries, integrity and bounded work

**Status:** accepted
**Date:** 2026-10-03
**Context:** [Phase 0–4 audit](../reviews/phase-0-4-audit.md)

## Decision

Keep the local PostgreSQL modular monolith and public CRUD contracts. Enforce
unique itinerary day/order and paired, ranged place coordinates in SQL.
Serialize aggregate mutations on the trip row and use shared root locks for
multi-query aggregate reads. Refresh ORM state before using a locked aggregate.
Place metadata remains owner-scoped reusable state with its own update lock.

Timed cross-day moves preserve wall-clock times on the destination date, with
DST gap/fold validation before applying the move. Immediate SQL uniqueness uses
two-stage renumbering inside the same locked transaction. Migration 0005 repairs
legacy moved dates and order; ambiguous repairs stop for operator correction.

A local configured owner is not authentication. Bind development services to
loopback and reject unconfigured hosts/browser origins at both API and web
proxy boundaries. Limit JSON requests to 64 KiB, do not echo private input or
exceptions, and provide request IDs, safe status/duration logs and SQL readiness.

External operations use immutable database projections outside transactions,
synchronous SQL in worker threads, streamed bounded response readers, and an
elapsed deadline independent of per-I/O timeouts. Research evidence expires at
the earliest citation/session deadline and never becomes authoritative state.

## Consequences and alternatives

Application validation alone does not protect against corrupted order/coordinates
or stale ORM collections. SQL constraints and real concurrent PostgreSQL tests
are required. Shared reads can briefly delay writes; bounded database waits
return a recoverable error instead of waiting indefinitely. A repeatable-read
whole-aggregate redesign or generic unit-of-work framework is not needed for
this personal local product.

Root locking protects itinerary/reservation consistency, not cross-tab user
intent or independently shared place metadata. Phase 5 must introduce explicit
versions for all proposal dependencies and manual mutation checks. No auth,
queue, proposal store, blob store or hosted deployment is introduced here.

CORS alone cannot stop writes from hostile pages. Host/origin guards are a
local boundary and cannot authenticate clients that omit Origin. Auth and
provider quotas must precede private ingestion/public deployment.

## Portability qualification

PostgreSQL/Neon is the supported persistence target. Aurora DSQL is a possible
future migration, not a drop-in URL substitution. Its optimistic concurrency
uses commit conflicts and retries rather than PostgreSQL's blocking row-lock
serialization. It now supports foreign keys, but that does not validate this
application's transaction/migration behavior. A DSQL decision must test bounded
transaction retries, constraint and migration support, driver/session behavior
and canonical data conversion.

Sources checked 2026-10-03:
[AWS SQL dialect and concurrency](https://aws.amazon.com/blogs/database/dsql-sql-dialect-how-amazon-aurora-dsql-differs-from-single-instance-postgresql/),
[AWS foreign key constraints](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-foreign-key-constraints.html).

