# Phase 6 identity foundation — local checkpoint

**Status:** P6.0/P6.1 identity review remediation implemented locally; independent re-review pending. Phase 6 booking/document import remains planned.
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

## Identity review remediation checkpoint

The six findings in [the independent identity review](../reviews/phase-6-identity-review.md)
have local fixes pending coordinator re-review. The centralized typed web client
adds session CSRF proof to unsafe writes and rejects duplicate or malformed
CSRF cookies. Owner migration transfers validated nested proposal snapshot
owners, rejects foreign references, and marks in-flight remote results failed
because their old-owner keys cannot be reconciled under the new owner. Google
service assertions now validate realistic numeric `sub`/`azp` identity claims.
OAuth code exchange and signing-key reads have elapsed deadlines; synchronous
auth SQL and token verification run outside the event loop, with worker-owned
callback SQL sessions. Only authoritative Gmail or configured verified
Workspace identities are accepted, and the current owner allowlist is checked
on every session lookup. Authenticated AI transport requires the configured
HTTPS destination and an explicit custom-audience setting when needed. Google
mode fixes cookie names to the web proxy's contract.

After these changes, the full migrated disposable-schema PostgreSQL suite
passed **180 tests, 0 skipped**; Ruff check and format check, and mypy over 75
source files passed. Pinned pnpm 10.17.1 passed **30 frontend tests**, lint,
typecheck and optimized production build. A repeatable mounted production
Next.js plus synthetic FastAPI smoke ran sign-in state/nonce and signed-token
verification through the real callback, typed trip creation/list/delete via
the same-origin proxy, missing-CSRF rejection, logout/revocation and private
page protection. No Google or Cloud Run endpoint was contacted. This mounted
check used HTTP requests with a browser cookie jar; a visual browser session
was separately checked for private-page redirection before login.

The mounted check uses a disposable local PostgreSQL database and the
explicitly guarded `frontend/scripts/mounted-identity-smoke.mjs` with
`backend/tests/fixtures/synthetic_identity_server.py`. It is synthetic
integration evidence, not a live provider/deployment result.

The identity deadline follow-up replaces the signing-key stream's renewable
per-read timeout with a live socket watchdog, tested against a real local
HTTP server trickling both headers and body bytes. OAuth callback work now
shares one 7.5-second deadline beneath the Next.js eight-second timeout;
worker-owned SQL transactions check it before commit. A disposable PostgreSQL
test confirms an abandoned session insert rolls back. The full backend suite
passed **182 tests, zero skipped**; Ruff and mypy passed. No live Google or
Cloud Run endpoint was used in these checks.

The subsequent callback deadline correction converts the remaining
`time.monotonic()` duration at every async timeout boundary, including code
exchange, so Uvicorn's `auto`/uvloop and `asyncio` clocks both honor the same
7.5-second budget. Auth SQL transactions set PostgreSQL transaction-local
statement and lock timeouts from that remaining budget; the OAuth-attempt
delete and session insert are flushed and checked before commit. A real
PostgreSQL table lock test confirms a blocked session insert fails and rolls
back. Mounted synthetic HTTP callbacks succeeded and slow exchanges timed out
under both Uvicorn loop settings. The full migrated-schema backend suite passed
**185 tests, zero skipped**; Ruff and mypy passed. These checks did not contact
Google or Cloud Run.

The signing-key socket watchdog interrupts an established socket during
blocked header/body reads. It does not interrupt DNS resolution before a
socket exists, and Python/OS address resolution and multi-address connection
cleanup can exceed the three-second key-fetch target. The synchronous verifier
runs in an abandoned worker with a deadline check before returning its result;
that transport limit is not a proof of a strict wall-clock bound on worker
termination. PostgreSQL connect/pool acquisition has separate finite timeouts;
transaction-local statement/lock limits begin after a connection is acquired.

## Remaining gates

Independent coordinator security review is pending. No Google OAuth client, Cloud Run IAM binding or upstream user-identity deployment was provisioned; no live sign-in, hosted service invocation or private-input test occurred. Before enabling hosted AI or private imports, verify those live boundaries and accept a separate upstream booking/document extraction and retention contract. P6.2 storage/parser, P6.3 extraction, P6.4 confirmation and P6.5 import review UI are not implemented. Research/proposal and private-import gates stay off by default.
