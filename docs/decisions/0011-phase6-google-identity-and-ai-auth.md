# ADR 0011 — Phase 6 verified identity and AI credential separation

**Status:** accepted for the Phase 6 identity stage
**Date:** 2026-10-04
**Scope:** P6.0 design and P6.1 identity foundation only

## Context

The existing `owner_id="local"` value is a development seam, not proof of
identity. Phase 6 must establish verified single-owner identity before any
private booking source is accepted. Travel and `personal-ai-system` have
separate authorities and credentials. No accepted booking/document extraction
contract exists in `personal-ai-system`, so this decision does not define an
extraction payload, storage lifecycle, or import route.

Google's server-side OpenID Connect flow requires anti-forgery `state`, a
one-time `nonce`, server-side ID-token validation, and stable `sub`-based
identity rather than mutable email. Its current guidance recommends using a
pre-written library for validation; the Google `google-auth` library verifies
the token signature, issuer, audience, and expiry. Travel also bounds the
Google signing-key fetch to three seconds, uses the provider's HTTP cache
headers with a one-key in-memory cache, and rejects `iat` values more than 60
seconds in the future. The sources are Google's [OpenID Connect guide](https://developers.google.com/identity/openid-connect/openid-connect),
the [Google ID-token verification guide](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token),
and the [`google-auth` ID-token API](https://google-auth.readthedocs.io/en/latest/reference/google.oauth2.id_token.html).
Private Cloud Run invocation uses a separate Google-signed service ID token,
with the receiving service URL or configured audience and least-privilege
Invoker IAM binding, as described in [Cloud Run service-to-service
authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service).
Google documents service-account ID-token claims separately from end-user
identity in its [token types reference](https://docs.cloud.google.com/docs/authentication/token-types).

The upstream identity implementation maps the canonical issuer and verified
subject as `usr_` plus the first 32 hexadecimal characters of
`SHA-256(issuer + NUL + subject)`. Travel uses that exact mapping so an
upstream identity directory can recognize the same owner. A single normalized
email allowlist entry is required, and `email_verified` must be exactly true;
email is a gate and display attribute, never the durable owner key.
The allowed address must be Gmail, or its domain must match a separately
configured Workspace hosted domain and the verified token's `hd` claim.
Every request checks the current allowlist against the stored session email,
so changing the configured owner removes access from older sessions.

## Decision

Use Google's authorization-code server flow with S256 PKCE. Persist a
short-lived, single-use login attempt with a browser-bound random secret,
state hash, nonce, and PKCE verifier. The configured client secret, token
exchange, and verified ID token remain server-side. Reject mismatched issuer,
audience, signature, nonce, email allowlist, email verification, future issue
time, expired token, or unavailable signing keys. Verification/key errors fail
closed; they never fall back to the local owner.

Issue a 256-bit random opaque browser session. Store only its SHA-256 digest,
owner mapping, CSRF-token digest, creation time, expiry, and revocation time.
Sessions last at most eight hours, are non-sliding, and logout revokes the
current session. Deliver the token only in a Secure, HttpOnly, SameSite=Lax,
Path=/ host-only cookie. Unsafe browser methods also require exact allowed
Origin/Fetch-Metadata checks and a per-session double-submit CSRF token. A
server-side Next.js route owns the Google callback and sign-in/logout UI; the
same-origin API proxy forwards only the configured cookies and explicit
revision/CSRF headers. FastAPI independently resolves the opaque session on
every domain request before reading a request body or entering domain/provider
services. `/health`, `/ready`, and non-mutating OpenAPI documentation remain
public and return no owner data.

The short-lived Google user ID token is kept in a separate Secure, HttpOnly
cookie only when the Cloud Run AI transport is configured. In that mode the
travel session expires no later than 60 seconds before the Google ID token, so
an expired user assertion cannot leave a longer-lived authenticated session
that silently loses its upstream identity. The API proxy maps that cookie to
`X-User-ID-Token`; the independent service identity uses `Authorization:
Bearer ...` only on the backend-to-backend hop. The browser cannot set either
credential directly.
The web proxy and API use the same fixed `__Host-travel_*` cookie names; Google
mode rejects alternate names until both sides support an explicit shared
configuration. Authenticated AI requests require the configured HTTPS service
URL. The Cloud Run service audience must have that service origin unless an
operator explicitly enables a custom audience configured for that service;
the outbound client still pins the destination to the configured HTTPS URL.

`TRAVEL_AUTH_MODE=local` remains the explicit default for local CRUD and tests.
It is confined by the existing local host/origin boundary. Private source
handling is disabled in local mode. If a credential is supplied while local
mode is active it must not cause authentication to silently change modes; if
Google mode is enabled, missing or invalid credentials always fail closed.

Every travel-domain route derives its owner from the authenticated request
principal. Browser-supplied owner headers and request-body owner identifiers
are never authoritative. Research and proposal calls remain separate gates.
The upstream supports a Google user ID token through `Authorization` or
`X-User-ID-Token`, and a private Cloud Run service IAM boundary. If Travel
forwards a user ID token, the configured upstream user-token audience must
equal the Google OAuth client ID that issued it; Travel will not impersonate
the user or replace identity with a shared secret. The typed outbound context
keeps the user token and Cloud Run service credential in separate fields and
headers. Cloud Run IAM is transport identity only and does not establish the
travel owner. Live service identity, audience alignment, upstream identity
directory readiness, and provider operation remain separate external gates.

There is currently no accepted booking/document extraction capability or
retention contract. `research-v1` and `itinerary-proposal-v1` do not satisfy
that prerequisite. Private imports remain disabled until a separately
accepted upstream HTTP/authentication/retention contract exists; this ADR
does not create a replacement or enable private input.

## Local-owner migration

Do not claim local rows on first login. An operator runs the migration command
after a verified target identity mapping exists, inspects a dry-run report,
verifies a database backup artifact by SHA-256, and explicitly applies the
same run ID and plan digest. The operation takes a transaction lock, checks all
owner-scoped rows including proposals and their top-level JSON snapshot owner,
detects provider-place uniqueness collisions, updates owner IDs without
changing revisions or foreign keys, and writes an append-only audit record.
Failed updates roll back. The command is never run automatically and is not
used against real user data as part of local verification.

## Consequences

- Authentication and authorization become independent of the existing local
  owner setting.
- An opaque database-backed session supports explicit logout/revocation and
  server-side page/API checks without storing a long-lived Google token.
- Upstream user authentication is possible only while a separately configured
  short-lived Google ID-token credential is available; audience mismatch
  disables that path.
- A deployment must configure Google credentials, callback URL, one allowlist
  email, secure cookies, and the upstream identity boundary before enabling
  Google mode or private integrations.
- No Google client has been provisioned, no IAM role has been assigned, no
  cloud resource has been contacted, and no private input was used for this
  local implementation.
