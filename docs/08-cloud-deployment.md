# Cloud deployment

Status: hosted target architecture; deployment is not provisioned or verified
Date: 2026-10-08

Hosted identity/configuration guards and production containers are implemented
locally. Their presence does not establish a deployed Cloud Run environment or
production acceptance. See [current state](current-state.md) for the local and
hosted capability matrix.

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
- GCS remains a future hosted private-storage option; the existing local
  source/attachment store does not provide hosted durability.

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

`LocalSourceStore` on the local filesystem is the only implemented private
source/blob store. Hosted configuration rejects startup when either private
booking imports or trip attachments are enabled, because instance-local files
are not durable shared storage. Keep both features disabled in hosted mode
until a supported shared object store is implemented and accepted.

When hosted private-file support is authorized, evaluate a single-region GCS
bucket in an eligible region or another supported durable shared store.

Store object key, size, media type, hash, and ownership/reference metadata in PostgreSQL.

GCS is not implemented or authorized. This restriction is enforced by the
hosted settings validator, not just by this deployment guidance.

## Authentication prerequisite

The app may be developed locally with `owner_id=local`.

Before a public cloud deployment stores real trip reservations, imported
booking material, or private documents, provision and verify:

- Google OIDC identity and server-owned owner derivation,
- secure travel API ingress/session and CSRF/origin policy,
- travel-to-Personal-AI service authentication and user-audience alignment,
- review external provider data-use policies.

Do not treat Cloud Run being reachable only through an obscure URL as security.

The delivered local host/origin guards are not authentication. Do not disable
them or set a wildcard to publish the local owner. Future deployment must
explicitly configure web/API hosts and origins after identity/session and
service-authentication work. Build the public Geoapify tile key into the web
image; keep server search/routing credentials in Secret Manager.

Use `/health` for process liveness and `/ready` for database readiness. Phase
0–4 has bounded pool/connect/statement/lock waits and safe request IDs/logs;
Phase 9 must validate pool sizing, quotas, monitoring and backup restoration.
Migration `0005` requires online data inspection and a pre-migration backup;
do not use offline SQL generation as the complete deployment migration path.

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
