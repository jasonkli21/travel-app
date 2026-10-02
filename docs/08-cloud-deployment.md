# Cloud deployment

Status: target architecture; not implemented in scaffold  
Date: 2026-10-02

## Initial target

```text
Browser
  |
  v
Cloud Run: travel-web
  |
  v
Cloud Run: travel-api
  | \
  |  +---- HTTPS ---> personal-ai-system
  |
  +---- TLS ---> Neon Postgres

future:
travel-api ---> Google Cloud Storage
```

## Why this split

- Cloud Run matches the existing personal AI deployment model.
- Neon preserves real PostgreSQL semantics from local development.
- The travel domain avoids adopting Firestore solely for cloud symmetry.
- GCS is only introduced when binary/document storage exists.

## Cloud Run defaults

When implementation reaches deployment:

- request-based billing,
- `min-instances=0`,
- conservative CPU/memory,
- low `max-instances`,
- explicit health checks,
- authenticated/private API topology once real data is stored,
- budget and alerts,
- no always-on worker without a product requirement.

Current free-tier details are documented in `05-technology-choices.md`.

## Database

Production uses a Neon project/branch configured through `DATABASE_URL`.

Do not commit the URL.

The app should use pooled/serverless-safe connection settings after testing Neon behavior under Cloud Run.

The exact pooling configuration is deferred until deployment work because provider recommendations can change.

## Migrations

Cloud deployment must include an explicit migration step.

Do not run competing Alembic migrations automatically from every Cloud Run instance startup.

Preferred patterns to evaluate later:

- CI/CD migration job before service rollout,
- one-off Cloud Run Job,
- explicit operator command.

## Secrets

Use GCP Secret Manager for:

- `DATABASE_URL`,
- travel-to-AI service credentials,
- future maps/places credentials,
- future blob credentials if needed.

The browser must never receive database/provider secrets.

## Networking

Initial external Neon connectivity will use TLS over the public endpoint.

If the application later requires stronger private networking, re-evaluate database/cloud choice rather than complicating Phase 1.

## Object storage

When required, prefer a single-region GCS bucket in an Always Free-eligible region if practical.

Store object key, size, media type, hash, and ownership/reference metadata in PostgreSQL.

## Authentication prerequisite

The app may be developed locally with `owner_id=local`.

Before a public cloud deployment stores real trip reservations, imported email, or private documents:

- implement user authentication,
- derive owner identity server-side,
- secure travel API ingress/session,
- authenticate travel -> personal-ai calls,
- review external provider data-use policies.

Do not treat Cloud Run being reachable only through an obscure URL as security.

## AWS migration direction

AWS is not part of the initial deployment.

Potential later learning/migration exercise:

```text
Cloud/AWS compute
  -> Aurora DSQL for structured travel state
```

Keep this optional.

A future assessment must test SQLAlchemy compatibility, Alembic strategy, transaction retry behavior, unsupported PostgreSQL features, connection lifetime/pooling, and migration of canonical data.

Do not maintain dual-cloud deployments simply to preserve optionality.
