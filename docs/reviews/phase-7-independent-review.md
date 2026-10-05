# Phase 7 independent review — Luna XHigh handoff

Reviewed on 2026-10-05. Review the findings below in a fresh **Luna XHigh** session; reproduce and fix them within the accepted initial slice, then update release evidence. This review implements no substantive fixes.

## Scope and conclusion

Reviewed `52d80fc`, `69b6065`, `9e6b0a3`, and `4fa08e2`, using the inclusive diff `52d80fc^..4fa08e2`. Read the product, architecture, data model, roadmap, handoff, AI integration, Phase 7 plan, ADR 0014, release record, affected implementation, existing tests, and companion domain contracts/provider/service integration. Checked the pinned upstream `cb38e1b9aeeef304e0cae66b85f47fec284f4622` against the available upstream HEAD `24f75a7db96496aa90a42c2b3da546d4af1e6612`; relevant response contracts are unchanged. Runtime probes used the available upstream HEAD and in-memory repositories, never a real provider or private input.

**The implementation is not yet sound enough to close the initial slice.** The architecture is appropriate: typed HTTP boundary, default-off independent gates, bounded external work, no SQL locks held over HTTP, deterministic category/radius checks, explicit save, and separate Phase 5 preview/apply. The documented exclusion of price, dates, availability, accessibility, travel duration, hotels/flights/transit, and memory retrieval is justified by ADR 0014. Do not implement those unsupported capabilities as remediation. The broader Phase 7 goal remains incomplete by design; documentation correctly leaves its exit gate open.

Priorities: **P1** blocks a core supported workflow or permits an invalid authoritative save; **P2** is a concrete correctness, recovery, or verification gap. No P0 issue found.

## Actionable findings

### 1. P1 — Default radius prevents browser submission

**Files:** `frontend/components/trip-comparison-panel.tsx:99,251`.

The radius starts at `5`, but the number input has `min="0.1" step="0.5"`. Allowed values start at 0.1 and advance by 0.5; 5 produces a native `stepMismatch`. Browser constraint validation blocks the form before `submitComparison` runs. Ordinary integer and half-kilometer values, including 20, also fail.

**Expected fix:** align the minimum, step, and default with the intended positive radius choices and API bounds.

**Validation:** use a real rendered form to assert the default passes `checkValidity()` and clicking Compare dispatches one request. Cover representative valid values and zero/out-of-range rejection.

### 2. P1 — Comparison save uses the wrong authentication/capability gate

**Files:** `backend/src/personal_travel/api/middleware.py:222–256`; integration with `frontend/lib/proxy.mjs:270`.

The middleware detects comparison operations using `"/research/compare" in path`. That matches `/research/compare` but **does not match** `/research/comparisons/{id}/candidates/{id}/save`. Saves are classified as ordinary research. With comparisons enabled and research disabled, the proxy forwards the HttpOnly-derived user assertion, but the backend rejects it with `invalid_identity_header`; without the assertion, it does not build the required AI auth context. Comparisons incorrectly depend on the separate research gate.

**Expected fix:** classify both exact comparison route shapes under `personal_ai_comparisons_enabled`, preserving early owner/session/CSRF and AI identity checks.

**Validation:** middleware/proxy API tests in Google mode for compare and save with comparison on/research off, the inverse configuration, missing/wrong-owner user assertions, and unrelated paths. Assert authentication happens before body/provider access.

### 3. P1 — Save does not revalidate shared-place revision or expiry under transaction locks

**Files:** `backend/src/personal_travel/services/travel_comparisons.py:176–243`; `backend/src/personal_travel/services/saved_places.py:100–173`.

`save_candidate` checks the reference-place revision/coordinates, releases the read transaction, awaits upstream detail, and eventually writes through `create_from_comparison`. That transaction only rechecks the **trip** revision. An independent `PlaceService.update` during HTTP can move or edit the center without bumping the trip revision, so the save succeeds against a stale comparison. Evidence is also checked before the write acquires its trip lock; it can expire while waiting. The documented requirement is revalidation before an authoritative write, not merely before HTTP completes.

**Expected fix:** pass the immutable reference footprint and effective evidence deadline to the saving transaction. Lock trip then relevant place rows in the established order; reload owner/membership, revision and coordinates, and check freshness immediately before mutation. Reject stale/expired input without creating a place, saved relationship, or revision increment. Keep locks outside provider work.

**Validation:** real migrated PostgreSQL tests with separate sessions: pause detail retrieval, edit only the center place, resume and assert 409/no mutation; similarly cross expiry while waiting for the trip lock. Cover unchanged success and existing-candidate replay semantics.

### 4. P2 — Expected missing/conflicting/stale evidence turns the entire comparison into 503

**Files:** `backend/src/personal_travel/services/travel_comparisons.py:389–425,469–486,588–612`.

`_validated_candidate` raises whenever either required cell lacks one current verified claim. `_comparison_response` uses an all-or-nothing list comprehension, and `compare` maps the exception to `research_comparison_invalid`/503. Missing, conflicting, unverified, or expired evidence is a valid upstream comparison outcome. A single such row hides other valid candidates. Expired claims raise before the response's `expired` branch can run; the UI's unknown/insufficient/expired presentation is therefore not delivered for these cases.

**Expected fix:** distinguish valid but insufficient/stale evidence from malformed or foreign/corrupt evidence. Keep uncertain/ineligible rows clearly labeled, or safely omit them with an accurate aggregate state. Never let them pass hard checks or save. Preserve other usable rows and return explicit insufficient/expired outcomes where appropriate.

**Validation:** mixed good/missing/conflicting/stale rows, all missing, expired claim with a still-current observation, expired sources, and same-key replay after expiry. Keep invalid ownership/citation/version responses rejected. A targeted probe confirmed both missing and expired claims currently raise.

### 5. P2 — Different claim expiries incorrectly make shared evidence inconsistent

**Files:** `backend/src/personal_travel/services/travel_comparisons.py:516–534`.

The same observation commonly substantiates both type and location. For each claim the code creates a source response with `expires_at=min(observation.expires_at, claim.expires_at)`, then requires the repeated evidence response to be identical. Legitimate claims with different expiries produce `duplicate candidate evidence is inconsistent`. This contradicts using the earliest supporting expiry.

**Expected fix:** validate immutable observation metadata consistently, then merge each shared source using the minimum contributing claim/observation deadline. Do not treat a narrower valid claim TTL as metadata corruption.

**Validation:** one evidence ID supporting type/location with distinct current claim expiries must succeed and expose the earliest expiry; conflicting identity/URL/attribution metadata must still fail. Confirmed with a mutated valid upstream fixture.

### 6. P2 — Service-identity failures escape comparison error mapping

**Files:** `backend/src/personal_travel/clients/personal_ai.py:237–312`; comparison service exception handling.

`_outbound_headers` raises `PersonalAIError` when service IAM credentials cannot be acquired or are invalid. Neither comparison client method catches it, and the service catches only `PersonalAIComparisonError` and `ValueError`. Both lookup and detail therefore escape as unhandled errors instead of a safe capability-unavailable 503. Existing proposal/extraction client methods already handle this base error.

**Expected fix:** translate outbound authentication failures into the comparison error family for both methods. Distinguish failure before dispatch from an ambiguous dispatched lookup where practical.

**Validation:** token fetcher raises, returns empty, and returns oversized credentials; assert safe structured 503 responses, no provider dispatch, and no credential leakage. Probes confirmed the base exception escapes both methods.

### 7. P2 — Ambiguous comparisons can be resubmitted with new keys; retries can change upstream input

**Files:** `frontend/components/trip-comparison-panel.tsx:128–167,265`; `backend/src/personal_travel/services/travel_comparisons.py:88–141`; request schema.

After an unknown POST outcome the UI retains `retryRequest` but still enables Compare, which generates a new UUID for the unchanged request. Editing any form field also discards the retained key. This defeats the stated reconcile-before-new-work rule. Furthermore, Retry stores a place ID, while the backend resolves its current coordinates on every attempt: a center edit between attempts sends different normalized upstream inputs under the old key, causing an upstream idempotency conflict that is mapped to generic unavailable guidance.

**Expected fix:** explicitly track ambiguous versus definitive failures; preserve the unresolved request/key and prevent accidental fresh-key resubmission until resolved or deliberately abandoned under a documented policy. Freeze the downstream input footprint or reject a changed center before claiming to retry the same request. This needs a bounded request/snapshot design, not a generic workflow engine.

**Validation:** lost response after upstream commit, repeated click, changed form, center-place edit, trip refresh, same-key replay, and idempotency conflict. Assert no unintended second provider dispatch and accurate recovery guidance.

### 8. P2 — Lookup response is not correlated to the submitted request key

**Files:** `backend/src/personal_travel/services/travel_comparisons.py:293–334`.

Travel deliberately generates category/distance constraint IDs with `uuid5(request.idempotency_key, ...)`, but validates only their values. A comparison for another lookup with the same category/center/radius passes; a probe using a different query/key and replaced constraint IDs was accepted. Owner correlation alone does not satisfy the plan's wrong-session/request rejection requirement.

**Expected fix:** verify the expected deterministic constraint IDs and uniqueness as part of the lookup footprint. If the accepted response cannot prove the full request fingerprint, explicitly document that limit and use its available correlation primitives; do not invent upstream fields.

**Validation:** correct IDs accepted; wrong, swapped, duplicate, and previous-key IDs rejected even when constraint values match. Confirm same-key replay retains the expected IDs.

### 9. P2 — Reservation-only trip places cannot be comparison centers

**Files:** `frontend/components/trip-comparison-panel.tsx:37–55`; `backend/src/personal_travel/services/travel_comparisons.py:337–352`; `frontend/components/trip-workspace.tsx` panel props.

Both center discovery functions include itinerary and saved-candidate places, but omit reservation places. A geolocated hotel attached only to a reservation is already part of the trip and appears in the existing trip map, yet cannot center a nearby-food comparison. The UI can incorrectly claim no trip place with coordinates exists. This misses the broader planner intent of researching around booked anchors.

**Expected fix:** include owner-scoped reservation places in backend membership and frontend selection, deduplicating by place ID. Apply the same coordinate/revision checks; no reservation details need cross the AI boundary.

**Validation:** reservation-only place works, duplicates appear once, missing coordinates remain excluded, foreign/unattached places remain rejected, and outbound payload still excludes reservation data.

### 10. P2 — Phase 7 has no regression or category-contract tests

**Files:** `backend/tests/`, `frontend/tests/`, `docs/releases/phase-7-travel-comparison.md`.

The reviewed commits change no tests despite adding strict consumer models, two routes, provider orchestration, save transactions, and UI state. Existing green tests exercise earlier phases. The release acknowledges unrun suites and missing category fixtures; the initial slice must not be treated as verified until those gaps are closed.

**Expected fix/validation:** add meaningful tests for findings 1–9, each accepted category and wrong-type/radius boundaries, fake-source non-persistence, verified OSM save/reuse/attribution, foreign owner/IDs, malformed/oversized responses, unsafe links, whole-operation deadlines, gates off, late responses/stale UI, committed-save/failed-refresh recovery, and explicit Phase 5 handoff without bypassing preview/apply. Run migrated PostgreSQL suites with zero skips, frontend tests/lint/types/build, backend lint/types, and package checks; record exact results. Keep separately authorized live provider, Google OAuth, and IAM checks visibly open; no live calls/deployment are authorized by this handoff.

## Validation performed and limits

- Existing frontend Node tests: **42 passed** using the bundled Node executable.
- Existing backend suite: **109 passed, 118 skipped, 4 failed**. PostgreSQL-dependent tests skipped because `TEST_DATABASE_URL` was unset. The four failures were existing Google key-server tests unable to bind localhost sockets in the sandbox, not demonstrated Phase 7 regressions.
- Local upstream in-memory fake lookup produced two rows; Travel successfully parsed and projected the normal food comparison. Targeted probes demonstrated findings 4, 5, 6, and 8.
- Findings 1–3, 7, and 9 follow directly from the affected browser rules, gate predicates, transaction/control flow, and trip membership code. No browser or PostgreSQL concurrency reproduction was completed; the specified regression tests remain required.
- No live provider, Google OAuth, IAM, hosted CI, deployment, or private input used. No substantive code changes made. The review adds only this handoff document.

## Remediation follow-up — 2026-10-05

The implementation now addresses the ten findings within the initial comparison
slice. The follow-up does not close the broader Phase 7 exit gate.

| Finding | Remediation and regression coverage |
| --- | --- |
| 1. Radius form validity | Set the radius control to `min=0.5`, `max=20`, `step=0.5`, with a valid default of `5`; helper tests cover supported and rejected values. A real rendered-browser `checkValidity()` run was unavailable. |
| 2. Comparison auth gate | Match the exact compare and save routes under the comparison capability gate. Google-mode tests cover both routes with comparison enabled/research disabled, the inverse gate, and invalid user identity; proxy tests assert the HttpOnly-derived token is forwarded for both paths. |
| 3. Save freshness under locks | Freeze trip/place revisions and center coordinates in the comparison request; recheck trip, membership, place revision/coordinates, and effective evidence expiry inside the save transaction after taking the trip then place locks. Separate-session PostgreSQL tests cover a moved center, expiry while waiting for the trip lock, successful attributed save, and existing-candidate replay. These PostgreSQL tests were added but skipped locally because no migrated test database is configured. |
| 4. Uncertain evidence | Project missing, conflicting, stale, or expired claims as unknown/ineligible rows, preserve other usable rows, and report insufficient/expired states. Unit tests cover mixed and all-uncertain rows while keeping corrupt citations/owners rejected. |
| 5. Shared evidence expiries | Correlate immutable source metadata and merge shared evidence with the earliest claim/source expiry. A fixture with different valid claim deadlines checks this behavior; forged metadata remains rejected. |
| 6. Service identity failures | Convert outbound identity acquisition failures to safe comparison errors before dispatch. Tests cover token-fetch exceptions, empty/oversized tokens, malformed/oversized responses, unsafe URLs, and the full lookup deadline. |
| 7. Ambiguous retry recovery | Persist the immutable request and key in session storage, including reference coordinates and revisions; disable edited/new submissions while unresolved; offer same-key retry or explicit abandonment. Helper tests cover persistence across reload, fixed coordinates/key, ambiguity classification, and stale context during retry. |
| 8. Request-key correlation | Validate category/distance constraint UUIDs against the submitted idempotency key and reject missing, swapped, duplicate, or prior-key IDs. |
| 9. Reservation-only centers | Include located non-cancelled reservation places in the frontend selector and backend membership check, deduplicated by place ID. Tests cover reservation-only membership, cancellation, and deduplication. Reservation details remain outside the upstream request. |
| 10. Regression evidence | Added four-category typed fixtures, hard category/radius checks, fake-source non-persistence, source/ownership checks, response bounds, deadlines, proxy/auth gate cases, and PostgreSQL save transaction coverage. No migration was needed. |

### Verification after remediation

- Backend Ruff check/format and mypy passed; focused client/comparison tests
  passed: **50 passed, 3 skipped**. The skipped cases require PostgreSQL.
- Full backend suite: **128 passed, 122 skipped, 4 failed**. The four failures
  are Google key-fetch tests that cannot bind their local HTTP server in this
  sandbox (`PermissionError: Operation not permitted`). PostgreSQL-dependent
  tests were skipped because `TEST_DATABASE_URL` is unset. An ephemeral local
  Postgres server could not initialize because the sandbox cannot allocate its
  required shared memory segment.
- Frontend ESLint, `next typegen`, TypeScript `--noEmit`, and production build
  passed; Node tests: **47 passed, 0 skipped**. The actual browser form validity
  probe was not run.
- No live provider, Google OAuth, service-IAM, deployment, or private input was
  used. These external gates and the broader Phase 7 exit criteria remain open.


## Independent remediation audit — 2026-10-05

Audited `f1c665cb81dedd950d2e17825be7ebdd14f8dedc` against all ten original
findings. The remediation is substantially correct. Four remaining gaps were
closed in this audit:

1. **P2 — Multi-source freshness:** `_claim_is_current` used `any`, permitting
   a claim to pass with an expired or future-dated supporting observation when
   another was current. Require all referenced observations to be current.
   Added regression cases with one current and one expired/future observation;
   the candidate remains ineligible and cannot become a saveable lead.
2. **P2 — PostgreSQL fixture shape:** the new save fixture represented
   registration fields as a dictionary, while the accepted contract iterates
   field objects with `.key`. Fixed the fixture to use the contract's sequence
   shape. All three separate-session/save tests now execute successfully.
3. **P2 — Blocked storage:** reading `window.sessionStorage` occurred before
   the helper's try/catch, so a browser SecurityError could crash the panel
   instead of using its promised in-memory fallback. Added a safe storage
   accessor and a test with a throwing browser storage getter.
4. **P2 — Logout retention:** newly persisted comparison queries/coordinates
   survived explicit logout in both session storage and the module cache.
   Successful logout now clears only comparison retry entries, including the
   memory fallback. Tests verify persisted and in-memory cleanup while retaining
   unrelated browser state. Natural session expiry still retains the exact key
   for deliberate same-account reconciliation after sign-in.

Validation: **256 backend tests passed with zero skips** against PostgreSQL
16.15, real migrations, and disposable schemas in a dedicated audit database;
**49 frontend tests passed**. Ruff check/format, mypy, frontend lint/types, and
production build passed. The initial full database run had two failures because
its database name did not satisfy the existing synthetic identity fixture's
`test`-name guard; rerunning in a correctly named dedicated database passed the
entire suite. No application fix to that guard was needed.

A temporary frontend route mounted the actual comparison panel in isolated
headless Chrome with synthetic intercepted HTTP. Default native form validity,
one request on submit, new-submission blocking after uncertainty, identical
request/key after reload and retry, deliberate abandonment, and valid/invalid
radius choices passed with no page errors. The route and server were removed
and stopped after verification.

The ten original local remediation findings are closed after these corrections.
The broader Phase 7 exit remains open for provider policy/live synthetic
verification and unsupported upstream capabilities; all capability gates remain
default off. No cloud configuration, provider request, real private input,
or companion-repository implementation changes were made.
