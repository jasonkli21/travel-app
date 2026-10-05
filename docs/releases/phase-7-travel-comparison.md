# Phase 7 initial travel comparison slice

Date: 2026-10-05

## Delivery status

The initial local comparison slice is implemented behind independent default-off
gates. The full Phase 7 exit gate remains open: category fixture coverage and
live provider policy/credential checks have not been completed. No provider,
OAuth, service-IAM, deployment, or companion-repository configuration was
changed.

## Accepted contract and behavior

ADR 0014 pins `personal-ai-system` revision
`cb38e1b9aeeef304e0cae66b85f47fec284f4622`, with `domain-lookup-v1`,
`domain-comparison-v1`, `domain-module-v1`, `travel-comparison-v1`,
`travel-features-v2`, and `travel-sources-v1`. Travel calls the typed upstream
lookup and owner-scoped comparison detail through `PersonalAIClient`.

Travel supports food, activity, neighborhood, and day-trip place leads. A
request includes the category, query, a trip place with coordinates, radius,
bounded result count, and a stable idempotency key. Travel sends only the
category-tagged query and reference coordinates with two required fail-closed
constraints. It excludes trip/place identifiers, trip title, itinerary and
reservation content, notes, booking data, and source references. It sends no
memory preferences. The trip transaction ends before upstream work begins.

The consumer validates owner and contract versions, requested constraint
footprint, typed place type/location claims, citation/source correlation,
public URLs and freshness. It recomputes category and Haversine distance in
Travel, and presents bounded source attribution, outcomes, and expiry. Unknown
or expired evidence cannot pass the hard checks. Result state remains in the
UI; no comparison migration or durable Travel comparison record was added.

Saving is a separate explicit action. Travel reloads the owner-scoped upstream
comparison and repeats the source, ownership, category, radius, expiry, and
rights checks. It also checks the current trip and reference-place revisions and
the reference coordinates against the comparison scope before a revision-checked
write. Coordinates and provider identity come from the verified source;
only reviewed name, address, category, and note fields come from the browser.
The existing trip and place revision rules apply. Synthetic adapter results
cannot be saved as sourced places. Drafting an itinerary change starts the
existing Phase 5 proposal flow and does not bypass preview or apply.

The accepted provider contract does not expose reliable schedules, dates,
hours, prices, taxes, accessibility, availability, party suitability, or travel
duration. These remain unknown and cannot be hard constraints. Hotel, flight,
transit offers, and memory retrieval are unsupported. The upstream has no
accepted preference-retrieval endpoint; this consumer does not invent one.

## Gates

- Travel API: `PERSONAL_AI_COMPARISONS_ENABLED=false` by default.
- Web build: `NEXT_PUBLIC_TRAVEL_COMPARISONS_ENABLED=false` by default.
- Upstream domain, provider, source policy, identity, and service-IAM gates
  remain separately controlled.
- Real Nominatim use needs the operator's current policy approval, contact
  configuration, and provider verification. No live request was made.
- Cloud Run IAM, Google OAuth, and cloud deployment remain unprovisioned.

## Local verification

- Backend: Ruff check and format passed; mypy passed for all 90 source modules.
  Focused comparison/client tests passed (50 passed); three migrated PostgreSQL
  tests were skipped because `TEST_DATABASE_URL` is unavailable.
- Full backend suite: 128 passed, 122 skipped, and four failed. The failures are
  Google key-fetch tests whose local HTTP server cannot bind a socket in this
  sandbox (`PermissionError: Operation not permitted`). PostgreSQL-dependent
  tests, including separate-session save locking and stale-center cases, remain
  unverified here. Docker and a PostgreSQL listener are unavailable; local
  PostgreSQL initialization also failed because this sandbox cannot allocate
  the shared memory segment Postgres requires.
- Frontend: ESLint, `next typegen`, TypeScript `--noEmit`, the production build,
  and all 47 Node tests passed. The radius input now has matching default, min,
  max, and step values; a rendered browser `checkValidity()` run was unavailable.
- No database migration was needed. No live provider, OAuth, service-IAM,
  deployment, or private input was used. Provider policy verification and the
  full Phase 7 exit gate remain open.

The initial independent review findings and their code/test disposition are
recorded in [`../reviews/phase-7-independent-review.md`](../reviews/phase-7-independent-review.md).
