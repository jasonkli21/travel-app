# Phase 6 identity foundation — local checkpoint

**Status:** P6.0 identity decision and P6.1 implementation complete locally; independent review pending. Phase 6 booking/document import remains planned.
**Date:** 2026-10-04

## Delivered identity boundary

- ADR 0011 accepts Google OIDC with a single verified-email allowlist and a stable issuer/subject owner ID compatible with the pinned upstream mapping. The server verifies signature, audience, issuer, authorized party, issue/expiry time, email and nonce using `google-auth`, with bounded signing-key retrieval.
- Migration `0009` adds verified identities, hashed opaque sessions, one-use OAuth login attempts and append-only migration audit. The browser session uses Secure, HttpOnly, SameSite cookies; unsafe methods require same-origin and a session-bound CSRF token. Logout revokes the stored session. The configured session lifetime is capped at eight hours.
- All existing domain routes resolve ownership from the request principal before body parsing or service/provider calls. Browser owner headers are rejected. Local mode remains an explicit unauthenticated development mode; it rejects supplied credentials and has no private import routes.
- Next.js validates the public host, handles the Google callback, forwards only allowed cookies and headers through the same-origin proxy, and checks the API session before rendering private pages. Sign-in, sign-out and expiry flows are present.
- Research and proposal calls use a typed outbound context with separately verified Google user and Cloud Run service ID tokens. Both AI gates remain off by default. The configured user-token audience must match the OAuth client, and the service identity is limited to the configured Cloud Run audience and account.
- The operator migration command requires an existing verified target, dry-run graph digest, backup artifact SHA-256 and explicit source-to-target confirmation. It locks and replans the graph at apply time, detects collisions, preserves travel revisions and records an audit row. It is never triggered by sign-in.

## Local verification

- Full migrated disposable-schema PostgreSQL backend suite: **170 passed, 0 skipped** against the dedicated local PostgreSQL 16 test database. This covers auth route families, signed synthetic tokens, OAuth state/nonce/single use, session expiry/revocation, CSRF, cross-owner access, AI identity separation, migration collisions/rollback, prior phase regressions and migration round trip.
- Ruff check and format check: passed. Mypy: passed for 75 source files.
- Frontend pinned pnpm 10.17.1: lint, typecheck, **28 tests** and optimized production build passed.
- After the eight-hour settings cap correction, the backend suite and static checks were rerun.

The tests use synthetic signed credentials and fake provider responses. A mounted end-to-end browser sign-in/logout run has not been performed; the proxy and browser-flow logic were exercised through automated boundary tests.

## Remaining gates

Independent coordinator security review is pending. No Google OAuth client, Cloud Run IAM binding or upstream user-identity deployment was provisioned; no live sign-in, hosted service invocation or private-input test occurred. Before enabling hosted AI or private imports, verify those live boundaries and accept a separate upstream booking/document extraction and retention contract. P6.2 storage/parser, P6.3 extraction, P6.4 confirmation and P6.5 import review UI are not implemented. Research/proposal and private-import gates stay off by default.
