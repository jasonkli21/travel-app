# Phase 7 implementation plan — richer evidence-grounded travel research

**Status:** initial source-bounded comparison slice implemented locally; full Phase 7 exit gate remains open
**Date:** 2026-10-05
**Baseline:** reviewed Phase 0–4 commit `56c0cbf`
**Dependencies:** [Phase 5](phase-5-implementation-plan.md) proposal safety and
[Phase 6](phase-6-implementation-plan.md) verified ownership
**Roadmap:** [phased implementation plan](09-implementation-plan.md)

## Delivery status

The first local slice supports food, activity, neighborhood, and day-trip place
leads using typed `place_type` and `location` evidence. Travel checks category
and radius deterministically, snapshots trip/reference-place revisions, releases
SQL locks before upstream work, and keeps comparisons transient. Candidate save
revalidates owner, source, rights, and freshness before a revision-checked
transaction; itinerary handoff remains a separate Phase 5 proposal action.

This slice does not implement dates, price/budget, hours, accessibility,
availability, travel time, memory retrieval, hotel/flight/transit offers, or a
travel-owned comparison table. The accepted upstream contract does not provide
those data. Synthetic fixtures cannot be saved as real places. Live provider
approval and category fixture coverage remain open; see the
[Phase 7 release record](releases/phase-7-travel-comparison.md) and
[ADR 0014](decisions/0014-phase7-travel-comparison.md).

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
| Contract | Pin the accepted `domain-lookup-v1` and `domain-comparison-v1` contracts for this typed consumer. `research-v1` remains a separate bounded-question API. Record category/type sets, source rights, deadlines, same-key replay and owner-scoped result references. |
| Initial categories | Choose a documented supported subset of food/activity/neighborhood/day-trip candidates. Hotel/flight/transit capabilities need category-specific evidence and validation before enablement, rather than one generic bag of fields. |
| Constraints | User-authored dates/timezone, geographic scope, party/accessibility requirements and optional budget are typed inputs. Existing itinerary/reservation anchors remain immutable context, with private confirmations/notes excluded. |
| Money | Compare integer minor-unit amounts in one explicit currency. A quoted price requires observation/expiry, total-vs-per-person basis, taxes/fees inclusion and party/date basis. Unknown totals or currencies cannot satisfy a hard budget; no implicit FX conversion. |
| Availability | Unknown, externally observed available/unavailable and user-confirmed requirements are distinct. Lack of evidence is not availability. External observations can fail eligibility without becoming a permanent database fact. |
| Ranking | Travel validates hard constraints and eligibility deterministically; AI may explain/rank only the eligible/uncertain set. A fluent explanation cannot override a failed budget/date/required-field rule. |
| Memory | Authenticated, opt-in retrieval of a narrow accepted preference projection. Show which preferences influenced the comparison and allow override. No silent memory writes or transmission of full user profiles. |
| Evidence | Require safe citations with observation/expiry and enough structured basis to substantiate each material claim. Do not invent evidence dates, retain disallowed provider data or copy the upstream research/evidence graph. |
| Work | One explicit bounded request/session at a time. No automatic category/provider fan-out, background polling or new retry keys after unknown outcomes. Longer-running support needs an accepted resumable contract and an ADR before enablement. |
| Mutation | Saving a place uses explicit reviewed fields through travel services; arranging items uses a new Phase 5 preview/apply action. Viewing or ranking candidates never writes travel state. |

ADR 0014 settles the initial geographic meaning as a point and radius. The
current slice has no schedule or price basis because the accepted provider
contract lacks those fields. Limit requests to ten candidates and the existing
external request budget. Category fixture examples remain a Phase 7 exit gate.

## Travel request/result design

Keep the existing /research endpoint and manual research-v1 panel compatible.
Introduce a distinct typed comparison path rather than changing its response
shape into a different product.

| Method | Route target | Behavior |
| --- | --- | --- |
| POST | /v1/trips/{trip_id}/research/compare | Verified owner, category, query, selected trip-place ID, radius, result limit and request key; snapshot current revisions, call accepted upstream and validate candidates. |
| GET | Upstream only: /v1/domains/travel/comparisons/{comparison_id} | Used internally for a separate explicit save; the Travel API exposes only its bounded compare response and save action. |
| POST | /v1/trips/{trip_id}/research/comparisons/{comparison_id}/candidates/{candidate_id}/save | User reviews name/address/category/note; Travel supplies source-verified coordinates and provider attribution to the existing saved-place transaction. |
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

The current form uses category, query, a geolocated reference trip place,
radius and result limit, with an explicit disclosure. It has no memory opt-in
or date/price fields because the accepted contract does not support those data.
The UI labels category and radius as hard checks and surfaces unknown details.

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

1. docs: accept the typed travel comparison and rights contract.
2. feat: add the gated comparison client, deterministic checks and reviewed save.
3. feat: add the comparison UI and Phase 5 proposal handoff.
4. docs: record the implemented slice, verification and open gates.

Phase 7 remains open until category fixtures, evidence freshness, rights and
explicit state handoff are verified for each enabled category. Current code is
an initial local slice, not proof of live provider coverage. Unsupported
hotel/flight/transit categories remain disabled and separately planned. Phase
9 must operationalize the actual provider cost/rights profile; this phase does
not assume free quotas or guarantee booking availability.
