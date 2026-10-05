# ADR 0014 — Phase 7 travel comparison contract

Date: 2026-10-05

Status: Accepted for the initial local comparison slice; provider and memory
gates remain closed.

## Context

The existing `research-v1` contract returns a cited answer, not a typed
candidate list. Phase 7 therefore uses the accepted `personal-ai-system`
domain lookup and comparison contracts instead of parsing research prose.
Travel remains the authority for itinerary state; domain comparisons are
transient research results until a traveler explicitly saves a candidate.

The upstream contract is pinned to
`personal-ai-system` revision
`cb38e1b9aeeef304e0cae66b85f47fec284f4622` and versions
`domain-lookup-v1`, `domain-comparison-v1`, `domain-module-v1`,
`travel-comparison-v1`, `travel-features-v2`, and `travel-sources-v1`.
Travel calls only the documented owner-scoped
`POST /v1/domains/travel/lookup` and
`GET /v1/domains/travel/comparisons/{comparison_id}` routes through its typed
HTTP client. It does not import upstream code or storage schemas.

## Decision

The first supported categories are food, activities, neighborhoods, and day
trips. They are a Travel API request taxonomy mapped to a required `place_type`
set constraint; the upstream contract has no separate category discriminator.
Travel independently checks each returned type against its category set and
checks coordinates against a required radius around a traveler-selected place
already attached to the trip. The submitted query and those coordinates are the
only trip-related inputs sent to the AI service/provider. Trip IDs, place IDs,
titles, itinerary and reservation content, notes, booking data, and source
records are excluded.

This initial slice supports only source-backed place identity, type, location,
distance, eligibility, ranking, and observation freshness. Nominatim place
lookup does not provide reliable opening hours, accessibility, prices, live
availability, party/date suitability, or travel duration. Those attributes
cannot be hard constraints or recommendation claims here. Hotels, flights,
transit options, merchant offers, and bookings remain unsupported.

Travel comparisons are held in the UI only. The API returns a bounded
owner-correlated projection with source links and expiry, while upstream
comparison IDs remain opaque and owner-scoped. A failed request may be retried
only with the same normalized input and idempotency key. No automatic retry
uses a new key, and no comparison session/table is added to Travel.

Saving a result is a separate explicit action. The Travel API reloads the
owner-scoped upstream comparison, revalidates its contract, candidate,
category, evidence freshness, source identity, and rights metadata, then
creates or reuses a trip candidate in one Travel transaction. Only a verified
OpenStreetMap/Nominatim result carries provider attribution into the saved
place. Synthetic results are marked and cannot be saved as real places.
Itinerary changes remain a separate Phase 5 proposal preview/apply flow.

The upstream currently accepts typed caller-supplied preferences but exposes
no accepted preference-retrieval endpoint. This slice does not fetch memory or
infer preferences. A later opt-in memory projection requires a separately
accepted owner-scoped upstream contract, version/fingerprint semantics, and a
disclosure/review before enablement.

`PERSONAL_AI_COMPARISONS_ENABLED` defaults off. The upstream decision,
travel-domain, provider-policy, identity, service-IAM, and deployment gates
remain independent. The upstream fake adapter remains the synthetic default;
real Nominatim use requires the operator's own current policy approval,
identity/contact configuration, and provider checks. No Cloud Run or provider
configuration is performed by this decision.

## Consequences

- Existing `research-v1` stays unchanged and available behind its existing
  gate.
- Hard category and radius rules are deterministic in Travel and cannot be
  overridden by upstream ranking.
- Unknown or expired evidence cannot be presented as a current eligible
  candidate.
- Only an explicitly reviewed save can create authoritative Travel state.
- Stored place attribution identifies the source; it does not make changing
  observations such as hours or availability durable facts.
- Preferences from memory and categories without an accepted evidence source
  remain disabled until a new contract decision.
