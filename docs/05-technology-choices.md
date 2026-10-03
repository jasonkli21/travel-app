# Technology choices

Status: accepted scaffold choices  
Date: 2026-10-02

## Summary

| Concern | Choice |
| --- | --- |
| Frontend | Next.js + React + TypeScript |
| Backend | Python + FastAPI + Pydantic |
| ORM | SQLAlchemy 2 |
| Migrations | Alembic |
| Local database | PostgreSQL 16 |
| Cloud database | Neon Postgres free tier initially |
| Compute | Google Cloud Run |
| Blob storage | local filesystem -> GCS when needed |
| AI integration | HTTP client to `personal-ai-system` |
| Python tooling | `uv`, `pytest`, `ruff`, `mypy` |
| JS tooling | `pnpm`, TypeScript, ESLint |
| CI | GitHub Actions |

## Why PostgreSQL

Travel state is highly relational:

```text
Trip
 -> days
 -> ordered itinerary items
 -> places
 -> reservations
 -> attachments
```

Expected operations benefit from foreign keys, joins, uniqueness constraints, transactions, ordered queries, and relational integrity.

Firestore or DynamoDB would work technically but would make the application's primary model less natural in exchange for cloud-specific free-tier benefits.

## Why local PostgreSQL permanently

The application should always be runnable locally.

This provides:

- no cloud dependency for development,
- a stable SQL target,
- easy test fixtures,
- data ownership/export,
- a fallback if a cloud free tier changes.

Local and cloud data are separate environments; the scaffold does not implement automatic synchronization.

## Why Neon initially

As of 2026-10-02, Neon announced its Free plan includes per project:

- 1 GB Postgres storage,
- 100 CU-hours/month,
- scale to zero/autoscaling,
- a managed PostgreSQL backend.

This gives the closest local/cloud database parity while keeping the app free at personal scale.

Source checked: https://neon.com/blog/neon-free-plan-1-gb-per-project

This is a deployment choice, not a domain dependency. `DATABASE_URL` remains the application seam.

## Why GCP Cloud Run

`personal-ai-system` already targets Cloud Run, and travel's API/web fit the same scale-to-zero container model.

As of 2026-10-02, request-based Cloud Run free usage includes:

- 180,000 vCPU-seconds/month,
- 360,000 GiB-seconds/month,
- 2 million requests/month,

based on `us-central1` pricing.

Source checked: https://cloud.google.com/run/pricing

Benefits:

- local Docker/container parity,
- ordinary FastAPI/Next.js servers,
- straightforward HTTP/SSE compatibility,
- scale to zero,
- shared operational ecosystem with `personal-ai-system`.

Free tier does not guarantee a hard zero bill. Budgets, quotas, low max instances, and authentication/rate limits still matter.

## Why not Cloud SQL initially

Cloud SQL would provide excellent managed PostgreSQL compatibility inside GCP, but it is not the strongest fit for a perpetual-$0 personal project.

Keep it as a future paid/production option if the application outgrows free services.

## Why not Firestore as primary travel state

The AI system uses Firestore successfully for its document/event-oriented workloads and vector retrieval.

Travel's app-owned state has different access patterns.

Do not force both repositories onto one database merely because they communicate.

## Why not DynamoDB

DynamoDB has a generous free tier but would require designing the data model around access-pattern partitioning instead of the application's relational graph.

This project should use the natural relational model rather than turn travel planning into a DynamoDB exercise.

## AWS portability

The architecture should remain compatible with a later AWS experiment.

Potential target:

```text
AWS compute
 -> FastAPI
 -> Aurora DSQL
```

But Aurora DSQL is not identical to PostgreSQL.

Preserve a portable subset:

- standard SQL,
- UUIDs,
- foreign keys,
- normal indexes,
- bounded transactions,
- application retries,
- no core dependency on PostGIS/extensions,
- no PL/pgSQL business rules.

Do not maintain two production implementations until an actual migration/learning objective exists.

## Blob storage

No blob service is required for Phase 0/1.

When attachments arrive:

```text
local: filesystem
cloud: GCS
```

As of 2026-10-02, GCS Always Free includes 5 GB-months Standard storage in eligible US regions.

Source checked: https://cloud.google.com/storage/pricing

Keep object metadata in PostgreSQL and bytes in the blob store.

## Maps/provider choice

Selected for Phase 3 in ADR 0007: Geoapify provides `osm-carto` raster tiles,
submitted free-text geocoding, and on-demand route estimates. The frontend
uses an in-repository XYZ tile renderer without a map-rendering package.
Revisit the provider before public deployment or a material usage increase.

Do not select Google Maps/Places merely because compute is on GCP.

Evaluate when needed:

- free quota,
- price,
- place coverage,
- attribution,
- terms for retaining provider IDs/data,
- geocoding/maps UI requirements,
- compatibility with `personal-ai-system` research adapters.

## UI libraries

The scaffold intentionally avoids locking in a heavy component library.

Likely candidates later:

- Tailwind CSS,
- shadcn/ui,
- TanStack Query,
- drag/drop library,
- a richer map renderer only if concrete interaction requirements exceed the
  small current pan/zoom/marker/route feature set.

Choose when Phase 1 UI requirements are concrete.
