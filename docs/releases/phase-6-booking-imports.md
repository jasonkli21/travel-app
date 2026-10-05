# Phase 6 — locally reviewed booking document import

**Status:** implemented and independently reviewed locally; external gates remain closed
**Date:** 2026-10-05
**Travel remediation commit:** `320201b485d35644a727a529910398e8e0d693be`
**Upstream remediation commit:** `ebd00a8e2fb2d8b59a5fb5fa3aa44268e5e79b63`
**Starting revisions:** travel `c036a5b`; upstream `ece8cfc`

Phase 6 covers verified owner identity and local source lifecycle, bounded
booking extraction, durable candidate review and recovery, atomic reservation
confirmation, and the accessible review panel. Travel pins the upstream commit
above and records it with extraction and confirmation outcomes. The local remediation pass and independent coordinator verification are
closed. Travel and upstream feature gates remain off by default; this record
does not authorize real private-input use or establish hosted readiness.

## Remediated behavior

The upstream `booking-document-extraction-v1` endpoint derives ownership from
its verified principal and requires explicit submit consent, a matching hash,
a stable UUID idempotency key, and at most 200,000 characters of UTF-8 text.
The authenticated route caps the body at 1,300,000 bytes and applies one
monotonic deadline across body receipt, claim, inference, and terminal
persistence. Real Gemini inference requires Google OIDC even in local app
mode. Fake generation is restricted to local/test with an explicit synthetic
fixture marker. Results enforce typed uncertainty, literal field evidence,
unique candidate IDs and source spans, safe terminal states, and expiry within
seven days. Raw text remains in request/service memory only; same-key replay,
detail recovery, and deletion tombstones preserve one owner-scoped identity.

Travel migration `0013` separates the original file digest from the extracted
text digest used by the upstream contract. Recovery preserves the same key
across a pre-dispatch interruption, reconciles unknown results by GET, and
terminates conclusive pre-acceptance client errors. Confirm, reject, source
delete, and cleanup calls carry the verified user and service context on the
allowlisted routes. Minimal owner/key/hash deletion intents survive rejection
regardless of original-source retention and survive trip deletion; the
owner-scoped retry endpoint bounds work by count and elapsed time. Local-to-
verified-owner migration refuses before any transfer when private source,
import, or deletion-intent records exist.

The review result provides an empty `duplicate_suggestions` list when there
are no candidates. Candidate values carry bounded evidence, strict uncertainty,
and schedule-pair checks. Corrected fields, including explicit null clears,
drive trip-local schedule projection while original evidence remains intact.
Created reservations require a traveler-selected tentative or confirmed
status, included in the confirmation fingerprint. Duplicate itinerary-item
assignments are rejected before batch writes. Confirmation stays atomic under
the trip-then-import lock order, and the booking confirmation logic now lives
in a focused service module.

The panel pins uncertain uploads but lets the user discard/reset after a
definitive client rejection. It blocks incomplete confirmation, requires
explicit uncertainty acknowledgement, normalizes travel reservation types,
and preserves recovery when a saved outcome cannot be refreshed immediately.
Panel and proxy regressions cover those flows.

## Verification

- Travel migrated PostgreSQL/API suite: **230 passed**, no skips, 39 warnings.
- Travel Ruff check: all source and test files passed; Ruff format check:
  **114 files already formatted**; mypy: **87 source files passed**.
- Frontend ESLint and TypeScript checks passed; Node tests: **42 passed**;
  the Next production build passed with
  `NEXT_PUBLIC_PRIVATE_IMPORTS_ENABLED=true`.
- Mounted synthetic flow passed through the production Next standalone
  server, authenticated proxy, Travel API, migrated PostgreSQL, and local
  upstream fake API. It exercised synthetic plaintext and an actual generated
  PDF file with synthetic content, review, confirmation and replay, source
  deletion, typed CRUD, CSRF rejection, logout, and private-page protection.
- The mounted run preceded the final upstream timeout-adapter correction.
  That final correction converts the same monotonic budget to the event-loop
  relative timeout; the shifted-loop regression and complete upstream suite
  were rerun on the final pin. The mounted PDF/text flow itself was not rerun
  after that timeout-only adjustment.
- Upstream backend suite: **561 passed, 12 skipped**, one Starlette warning.
  Ruff check and formatter check passed for all eight changed Python files.
- The upstream deadline regression deliberately offsets the event-loop clock
  while verifying that the service converts its shared monotonic deadline to a
  relative asyncio timeout. The earlier identity checkpoint records HTTP
  timing checks under Uvicorn `auto` and `asyncio`.
- `git diff --check` passed in both repositories before the documentation
  checkpoint commit.

The mounted document data was synthetic; no personal email or private booking
input was used. The PostgreSQL server was an existing local PostgreSQL 16 test
instance. The mounted fake API verifies local contract wiring and SQL effects,
not a live provider or cloud identity boundary.

The upstream full-repository formatter check has 78 unrelated pre-existing
unformatted files; every changed upstream Python file is formatted. The
upstream environment does not include mypy, so no upstream mypy result is
claimed. The pre-existing tracked upstream `frontend/tsconfig.tsbuildinfo`
modification was preserved byte-for-byte at SHA-256
`32aacb3de552bd4178722b010bc2270233802a02d2719f6fa8aa52e783a251b2` and was
not staged.

## Gates still open

Travel intake and extraction, upstream extraction, and provider calls remain
disabled by default. No live Google OAuth, Cloud Run IAM, production user or
service identity, Gemini request, cloud project, or real private document was
used. Provider data-use/retention approval, Firestore TTL configuration,
deployed service-audience alignment, Linux parser memory enforcement, and
cloud storage/deployment checks remain unverified. No Phase 7 work was started.

Read [ADR 0013](../decisions/0013-phase6-booking-document-import.md), the
[implementation plan](../phase-6-implementation-plan.md), the
[upstream contract](../../../personal-ai-system/docs/booking-document-extraction-contract.md),
and the historical [identity](phase-6-identity.md) and
[source-lifecycle](phase-6-private-sources.md) checkpoints. The next action is
coordinator verification of the whole-phase review findings; stop before Phase 7.

## Independent coordinator closure — 2026-10-05

The coordinator reviewed the whole phase against its written plan and the
system's ownership, privacy, replay, deterministic confirmation and UI intent.
All eleven findings were remediated in upstream `ebd00a8` and travel `320201b`.
The exact upstream code pin is accepted for the local implementation.

The final light pass additionally fixed source-expiry cleanup to enqueue the
minimal durable upstream-deletion intent in the same transaction that detaches
its source. Authorized operator retry can now find that intent without the
owner reopening each expired import. A PostgreSQL regression proves repeated
cleanup retains one intent with the extracted-text hash and preserves import
outcomes. One migration received a formatting-only correction.

Coordinator verification: **231 migrated PostgreSQL tests, zero skips**;
Ruff check and format check across **129 files**, mypy **87 source files**;
frontend **42 tests**; upstream **561 tests, 12 opt-in skips**. Previous lint,
typecheck, production build and mounted plaintext/PDF evidence above remains
valid; final mounted timing limits are stated precisely above. No outstanding
substantive local review finding remains. All live identity/service/provider,
retention-policy/Firestore, Linux parser and cloud gates remain as documented.
Work stops here before Phase 7 by explicit user instruction.
