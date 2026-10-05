# Phase 6 — booking document import candidate

**Status:** implementation complete as a local candidate; whole-Phase 6 review pending
**Date:** 2026-10-05
**Travel implementation:** `d596b1aa1847bf74e0e317eddb9b7fdc52f793fd`
**Upstream capability:** `ece8cfc3db044aab3b275709c12e71eb17f2520d`
**Starting revisions:** travel `cf6696b`; upstream `96cf73b`

Phase 6 now spans verified owner identity and local source lifecycle, a separate
bounded extraction contract, durable candidate review, atomic reservation
confirmation, and the responsive review panel. The travel client pins the exact
upstream candidate revision above and records it on each extraction and saved
confirmation. Both repositories keep their extraction gates off by default.
This release record is evidence for whole-phase review; it does not accept the
contract for real private-input use or establish hosted readiness.

## Delivered behavior

The upstream `booking-document-extraction-v1` endpoint derives ownership from
its authenticated principal and requires explicit submit consent, a matching
source hash, a stable UUID idempotency key, and at most 200,000 characters of
UTF-8 text. Its special authenticated request limit is 1,300,000 bytes. It
returns at most ten strict typed candidates with literal, server-validated
source spans and excerpts no longer than 240 characters. Document content is
data only: the capability does not access memory, search, tools, travel state,
URLs, or fetched links. Raw text is request/service memory only. The durable
store retains results for seven days, supports same-key replay and detail
recovery, and uses deletion tombstones to fence delayed POSTs. Fake generation
requires an explicit synthetic-fixture marker.

Travel migration `0012` adds durable extraction claims, the pinned upstream
revision, result expiry, confirmation key/fingerprint, and replay outcome.
Extraction POSTs run outside SQL locks; an uncertain result is recovered with
GET on the same key. Candidate snapshots are validated against the submitted
source hash and literal source spans. A source-content duplicate under another
request key returns 409 with instructions to use the original key; no alias is
created. Source deletion goes through the existing source lifecycle and
upstream delete-by-key path, with a durable retry flag if upstream cleanup is
unavailable.

The panel supports text paste and PDF upload, explicit boundary/retention
disclosure, source downloads/deletion, editable candidates, uncertainty and
timezone context, advisory duplicate choices, and saved-state recovery. The
owner must choose create, link, or skip for every candidate. Confirmation checks
trip and import revisions, uses a fixed trip-then-import lock order, applies the
entire selected batch through existing reservation rules in one transaction,
and stores candidate-specific outcomes. New reservations are tentative. A
replay with the same key/fingerprint returns the prior outcome. Raw source and
candidate excerpts are deleted by default after confirmation or rejection;
saved reservation outcomes remain.

## Verification performed

- Travel PostgreSQL/API suite: **218 passed**, 26 dependency deprecation
  warnings. The suite uses migrated disposable schemas and includes owner,
  idempotency, deletion/recovery, timezone/DST, rollback, and concurrent/replayed
  confirmation cases.
- Travel Ruff check and format check: **111 source/test files passed**.
- Travel mypy: **84 source files passed**.
- Frontend ESLint and TypeScript checks passed; **36 frontend tests passed**.
- Next production build passed with
  `NEXT_PUBLIC_PRIVATE_IMPORTS_ENABLED=true` for the candidate review build.
- The extended `frontend/scripts/mounted-identity-smoke.mjs` passed through the
  production Next proxy, synthetic verified-identity fixture, real travel API,
  migrated PostgreSQL schema, and local upstream fake adapter. It completed
  sign-in, upload, extraction, source-aware review, reservation creation,
  same-key confirmation replay, source/result deletion, and trip cleanup.
- Upstream suite: **553 passed, 12 skipped**, with one Starlette deprecation
  warning. Ruff check passed and all **12 changed Python files** pass the
  formatter check.
- `git diff --check` passed in both repositories.

The upstream full-repository formatter check still reports 78 unrelated,
pre-existing unformatted files; every changed upstream Python file is formatted.
The upstream virtual environment does not include mypy, so no upstream mypy
result is claimed. The frontend build regenerated the travel repository's
ignored `tsconfig.tsbuildinfo` cache; its pre-run bytes were not recorded, so it
was left as a disposable ignored cache. The pre-existing tracked modification
to `personal-ai-system/frontend/tsconfig.tsbuildinfo` was preserved and not
staged or changed.

## Gates and remaining evidence

Travel private intake and extraction, the upstream extraction capability and
provider call all default off. Private intake is unavailable in local auth
mode. No live Google OAuth, Cloud Run service IAM, production user/service
identity, model provider, cloud project, or real private document was used.
Provider data-use/retention approval, Firestore TTL configuration, deployed
identity and service-audience alignment, Linux parser memory enforcement, and
cloud storage/deployment checks remain unverified. The synthetic mounted flow
proves local contract wiring and travel SQL effects only; it does not establish
live or private-input readiness.

Read [ADR 0013](../decisions/0013-phase6-booking-document-import.md), the
[implementation plan](../phase-6-implementation-plan.md), the
[upstream contract](../../../personal-ai-system/docs/booking-document-extraction-contract.md),
and the historical [identity](phase-6-identity.md) and
[source-lifecycle](phase-6-private-sources.md) checkpoints. The next step is
independent whole-Phase 6 review; stop before Phase 7.
