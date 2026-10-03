# Phase 7 implementation plan — richer evidence-grounded travel research

**Status:** planned; no Phase 7 implementation delivered
**Date:** 2026-10-03
**Baseline:** reviewed Phase 0–4 commit `56c0cbf`
**Dependencies:** [Phase 5](phase-5-implementation-plan.md) proposal safety and
[Phase 6](phase-6-implementation-plan.md) verified ownership
**Roadmap:** [phased implementation plan](09-implementation-plan.md)

## Goal and scope boundary

Let a traveler compare a small set of grounded travel candidates against
explicit dates, locations, budget and preferences, with understandable evidence
and uncertainty. Extend the external research capability only where an accepted
contract supports it. Research remains evidence, not a reservation or itinerary.

Deliver focused activity/food/neighborhood/day-trip comparisons first, then
hotel/transit/flight research only behind independently verified category
contracts/provider gates. The phase's exit gate covers the accepted first
category set; unsupported categories are explicitly disabled, not placeholders
that imply coverage. Add structured constraints, bounded candidate comparison,
freshness/rights handling, optional consented memory preference retrieval and
explicit candidate/proposal handoff.

Defer buying/booking, price monitoring, continuous background research, travel
optimization engines, calendar/email sync, implicit memory writes, a travel
search agent, copying upstream evidence stores, guaranteed availability/live
traffic and an unbounded multi-category “plan everything” prompt.

## Design decisions and accepted upstream gate

| Concern | Decision |
| --- | --- |
| Contract | Pin a versioned accepted upstream travel-research/comparison contract. research-v1 can continue answering bounded questions; do not pretend its prose is a typed candidate list. Record supported categories, limits, source rights, deadlines, cancellation/replay and result references. |
| Initial categories | Choose a documented supported subset of food/activity/neighborhood/day-trip candidates. Hotel/flight/transit capabilities need category-specific evidence and validation before enablement, rather than one generic bag of fields. |
| Constraints | User-authored dates/timezone, geographic scope, party/accessibility requirements and optional budget are typed inputs. Existing itinerary/reservation anchors remain immutable context, with private confirmations/notes excluded. |
| Money | Compare integer minor-unit amounts in one explicit currency. A quoted price requires observation/expiry, total-vs-per-person basis, taxes/fees inclusion and party/date basis. Unknown totals or currencies cannot satisfy a hard budget; no implicit FX conversion. |
| Availability | Unknown, externally observed available/unavailable and user-confirmed requirements are distinct. Lack of evidence is not availability. External observations can fail eligibility without becoming a permanent database fact. |
| Ranking | Travel validates hard constraints and eligibility deterministically; AI may explain/rank only the eligible/uncertain set. A fluent explanation cannot override a failed budget/date/required-field rule. |
| Memory | Authenticated, opt-in retrieval of a narrow accepted preference projection. Show which preferences influenced the comparison and allow override. No silent memory writes or transmission of full user profiles. |
| Evidence | Require safe citations with observation/expiry and enough structured basis to substantiate each material claim. Do not invent evidence dates, retain disallowed provider data or copy the upstream research/evidence graph. |
| Work | One explicit bounded request/session at a time. No automatic category/provider fan-out, background polling or new retry keys after unknown outcomes. Longer-running support needs an accepted resumable contract and an ADR before enablement. |
| Mutation | Saving a place uses explicit reviewed fields through travel services; arranging items uses a new Phase 5 preview/apply action. Viewing or ranking candidates never writes travel state. |

The category ADR must settle geographic meaning (area vs point), schedule
coverage, price basis, rights/retention and required candidate fields. Limit
initial comparison to ten candidates and the existing external request budget
unless the accepted contract requires a separately justified bound. Require
source fixture examples for every enabled category.

## Travel request/result design

Keep the existing /research endpoint and manual research-v1 panel compatible.
Introduce a distinct typed comparison path rather than changing its response
shape into a different product.

| Method | Route target | Behavior |
| --- | --- | --- |
| POST | /v1/trips/{trip_id}/research/compare | Verified owner, selected day/date range, category, explicit constraints, memory consent and request key; snapshot current versions, project bounded context, call accepted upstream and validate candidates. |
| GET | /v1/trips/{trip_id}/research/comparisons/{comparison_id} | Only if the accepted capability supports durable detail/resumption; expose a bounded owner-correlated projection, not upstream records. Otherwise keep results in UI memory as in Phase 4. |
| POST | Existing saved-place/manual or provider-import route | User reviews candidate fields and explicitly creates/saves a place; provider identity/attribution may only be used when verified by the accepted place contract. |
| POST | Existing Phase 5 proposal route | Explicit request to arrange reviewed candidates; new snapshot, proposal validation and separate apply consent. |

Do not invent a travel-owned comparison database solely to retain UI results.
If resumption genuinely requires one, store only owner/trip association,
opaque upstream reference, input fingerprint/version footprint and expiry,
with defined retention. Ownership/session correlation must be verifiable; a
caller cannot retrieve someone else's upstream ID by substituting it.

Per-request constraints are sufficient initially. Add a persisted travel
constraint/preferences record only if saving those settings is part of the
accepted UX; do not add a generic rules table. A preference is not a hard
constraint unless the user explicitly makes it one. Accessibility/allergy
information is potentially sensitive: it is opt-in, minimally projected,
disclosed and never inferred from memory without consent.

Candidate contracts are discriminated by category. Common fields include
candidate handle, label, category, bounded explanation, eligibility/uncertainty,
safe source citations and observation/expiry. Category-specific typed fields
cover point/area, date/schedule, price basis/currency, provider identity and
rights as relevant. Reject extra fields, unsupported categories, invalid units,
NaN/ranges, unsafe URLs and false version/session correlation before rendering.

## Deterministic constraint and freshness policy

Validate dates and timezones against the trip and selected scope; preserve
Phase 0–4 wall-clock/DST behavior. Research does not alter confirmed anchors.
Use provider route durations only as time-bounded observations with the
existing buffer semantics; do not convert estimates into guaranteed transfers.

For a budget ceiling, compare only complete same-currency amounts with the
requested party/date/tax basis. Missing price components produce “unknown”,
not “within budget.” Explicitly unavailable or out-of-date observations cannot
pass a hard requirement. Price/current-hour/availability claims need current
evidence; general neighborhood background may use the accepted longer freshness
policy. Actual booking requires the traveler to verify externally.

Return the contributing constraint results so the UI can explain failures.
The server removes ineligible candidates from an “eligible” ranked list and
labels uncertain ones separately; it does not ask the model to enforce rules.
Empty or insufficient results are a supported outcome.

A result footprint includes trip revision, independent place revisions and
consented memory preference version if available. Hide stale output after input/
context changes, late responses or earliest claim/citation expiry. Memory
versions must not be faked with timestamps if the upstream contract has no
version primitive; use an immutable returned preference snapshot/fingerprint.

## Privacy, rights and lifecycle

Project only necessary labels/schedules and coarse geographic context. Do not
send documents, confirmations, source references, private notes or raw identity
tokens as model context. Narrow memory retrieval uses authenticated service
calls and explicit ownership mapping, not the local owner seam.

The upstream system owns provider search/evidence lifecycle. Travel validates
rights metadata before retaining any selected place/provider field. Retained
place identity/attribution describes the selected source; changing prices/hours
remain research observations. If rights do not permit retention, keep the
comparison transient and require manual user entry rather than bypass policy.

No automatic research-provider calls occur on workspace load, memory retrieval, tab
switch or ordinary CRUD refresh. A new comparison is explicit. Idempotency
correlates the exact normalized request; conflicting reuse fails. A remote
timeout leaves safe retry/reconcile guidance under the accepted upstream
contract, without a new autonomous request.

## Frontend behavior

Build a focused comparison form with category/date/location/constraint fields,
memory opt-in and disclosure. Show which constraints are hard, which preferences
are advisory and which fields remain unknown.

Render a compact accessible candidate table/cards with comparable dimensions,
units/price basis, per-claim citations, expiry and eligibility explanations.
Avoid ranking an unknown price above a verified in-budget option as if both
satisfy the same requirement. Support no-results, insufficient, unavailable,
unsupported, stale and expired states.

“Save candidate” reviews the permanent place fields separately. “Arrange in
itinerary” requests a Phase 5 proposal and navigates to its deterministic diff;
neither action implies booking. Saving after a committed write uses the
saved-but-refresh-failed recovery rule.

## Dependency map and work packages

~~~text
P7.0 accepted category/evidence/memory contract + rights ADR
               |
P7.1 typed inputs/candidates + deterministic eligibility
               |
P7.2 bounded authenticated research/memory client orchestration
               |
P7.3 comparisons + explicit save/proposal handoff
               |
P7.4 category verification + release
~~~

### P7.0 — Select the supported category slice

Pin upstream version, fake/provider fixtures, price/schedule semantics, source
rights, memory consent and result lifecycle. Record unsupported categories.

**Acceptance:** every enabled category has a concrete typed evidence contract;
no free-text parser substitutes for one.

### P7.1 — Implement deterministic comparison rules

Define discriminated DTOs, units/currency/basis validation, constraint results,
freshness and version footprint. Keep rules as small concrete helpers.

**Acceptance:** missing/unknown/expired observations cannot pass hard
constraints; category-specific failures are explainable and independent of AI.

### P7.2 — Integrate accepted research and optional memory

Add gated authenticated clients, narrow projection, request correlation,
streamed byte/deadline bounds and optional accepted resumption.

**Acceptance:** no private source data crosses the boundary; memory is opt-in;
no SQL locks span provider work and AI-off manual workflows remain intact.

### P7.3 — Deliver comparison and explicit state handoff

Add comparison UI, citations/basis/constraint explanations, stale/expiry handling,
reviewed place save and Phase 5 proposal handoff.

**Acceptance:** comparison alone changes no authoritative record; save/apply
requires separate explicit actions and revalidation.

### P7.4 — Verify categories and record release

Test all accepted category fixtures and one separately authorized live synthetic
path per enabled real provider. Update AI/product/data/rights/local/handoff
docs, capability matrix and exact release evidence.

**Acceptance:** supported vs mocked-only vs disabled categories are accurately
reported; external policy/credential failures remain visible gates.

## Failure and verification matrix

| Area | Required cases |
| --- | --- |
| Category contract | Supported/unsupported discriminator, missing/extra fields, wrong session/version/handle, oversized candidates/responses, unsafe citations, invalid coordinates/schedules/units |
| Constraints | Date/timezone/DST, party/total/taxes price basis, mixed currency, missing fees, zero/negative/large amounts, unavailable/unknown/expired hours, accessibility uncertainty |
| Ranking/evidence | AI recommends an ineligible option, insufficient citations, stale individual claims, current-vs-general freshness, unsupported retention rights |
| Memory/privacy | Consent off/on, wrong owner, changed preferences/override, sensitive excluded fields, service auth failure, no memory writes |
| Concurrency/work | Trip/shared-place edit during research, late response, expiry timer, exact-key replay/conflict, remote timeout/reconciliation, request budget/no automatic fan-out |
| UX/handoff | Comparable dimensions, explicit uncertainty, keyboard/mobile access, reviewed place save, Phase 5 preview/apply isolation, post-commit refresh failure |

Run complete migrated backend/API/contract tests, frontend lint/types/tests/build,
deadline/byte-limit tests and package checks. Add migrations only for actual
retention/version requirements and verify parity/upgrade on existing data.
Separate mocked contract coverage from live evidence/provider-rights approval.

## Commit sequence and exit gate

1. docs: accept category, constraint, rights and memory contracts.
2. feat: add typed deterministic travel comparisons.
3. feat: integrate gated rich research and consented preference projection.
4. feat: add comparison UI and explicit save/proposal handoff.
5. docs: record supported-category release and verification.

Phase 7 completes for the documented supported category set only when
eligibility, evidence freshness, consent and explicit state handoff are proven.
Unsupported hotel/flight/transit categories remain disabled and separately
planned. Phase 9 must operationalize the actual provider cost/rights profile;
this phase does not assume free quotas or guarantee booking availability.
