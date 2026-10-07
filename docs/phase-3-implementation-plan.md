# Phase 3 implementation plan — maps and travel logistics

**Status:** Accepted locally; see [release evidence](releases/phase-3-maps-logistics.md).

## Review corrections (2026-10-03)

The [Phase 0–4 audit](reviews/phase-0-4-audit.md) extends acceptance criteria;
the original provider decision and slice below remain the historical plan.

- Provider JSON is streamed and capped at 2 MB before assembly. Each external
  request has an elapsed deadline as well as an I/O timeout; logistics provider
  work is capped at 30 seconds overall.
- Estimate at most 50 eligible transfers and group at most ten waypoints per
  request without dropping joining legs. Bound geometry to 10,000 points per
  line. No provider call holds a database transaction or blocks the event loop
  with synchronous SQL.
- Use a circular longitude mean for search bias near the date line.
- Late search responses cannot appear for changed inputs. Same-turn provider
  submission/import/logistics guards prevent duplicate work.
- Manual coordinates can be entered/cleared as a pair in the place form;
  coordinate-free no-provider workflows remain usable.
- Production tile keys are build-time public configuration and backend keys
  remain private. Live provider coverage/rights/quota validation is a separate
  external verification gate, not proven by mocks.

**Status:** implemented locally; see [release record](releases/phase-3-maps-logistics.md)
**Date:** 2026-10-03
**Roadmap:** [`09-implementation-plan.md`](09-implementation-plan.md)
**Baseline:** Phase 2 reservations and saved places delivered locally
**Provider decision:** [`decisions/0007-geoapify-maps-and-logistics.md`](decisions/0007-geoapify-maps-and-logistics.md)

This plan delivers a manual-first map and logistics vertical slice on top of
the Phase 2 trip workspace. It closes Phase 3 provider questions before code
changes and keeps provider observations separate from durable app state.

## Scope

### Deliver

- A trip map with day selection and markers for itinerary, reservation, and
  saved-candidate places that have coordinates.
- Explicit, submitted text search for addresses and places through Geoapify.
- An import action that stores the selected result as an owner-scoped place and
  a trip-scoped saved candidate, reusing a previously imported provider place
  idempotently.
- On-demand route and travel-time estimates between consecutive, scheduled,
  geocoded itinerary items on a selected day.
- Deterministic transfer warnings when available schedule gaps are shorter
  than the provider estimate plus a user-selectable buffer.
- Visible provider/source attribution, missing-configuration behavior, API
  errors, and local setup instructions.
- Focused backend coverage for provider mapping, ownership, import idempotence,
  and logistics eligibility/warning rules, plus frontend map-geometry tests.
  Verify the UI integration with lint, TypeScript, and a production build.

### Defer

- AI research/proposals; this is Phase 4/5 work and no AI endpoint should be
  invented here.
- Authentication, deployment, booking imports, attachments, and cloud secrets.
- PostGIS, map-based editing/dragging, turn-by-turn directions, route
  optimization, live traffic guarantees, live transit schedules, place detail
  scraping, opening-hour refresh, bulk search, and background jobs.
- Automatic coordinate lookup for existing manual places. Users can continue
  editing place metadata manually; only explicitly imported provider results
  receive provider coordinates in this phase.
- Persisted route geometry/travel times. They are observations returned by the
  external provider, not durable facts owned by the travel database.

## Product and provider decisions

Use Geoapify for map tiles, submitted free-text location search, and route
estimates. An in-repository XYZ tile renderer displays the provider's
`osm-carto` raster tiles, so Phase 3 does not add a map-rendering dependency.
Use a backend-only key for search/routing and a separate referrer-restricted
browser key for map tiles. Keep keys out of source control and show map
attribution `© OpenStreetMap contributors` and `Powered by Geoapify`. Keep
source attribution beside imported provider results and places as well.

Geoapify currently lists a free tier of 3,000 credits/day, no credit card, and
up to five requests/second. Current published usage guidance estimates 0.25
credits per raster map tile, one credit per geocoding request, and one credit
per route waypoint pair plus a distance surcharge per 500 km. Search is
submitted (not autocomplete) and bounded to ten results. Route estimation is
on-demand for one selected day. There is no hard-coded assumption that these
limits or terms are permanent; recheck them before public deployment. The free
tier is soft quota and best-effort, so no-provider mode remains a supported
manual-planning state. Search uses the Geoapify Geocoding API's submitted
free-text query (the nearby category Places API is not part of this slice).
See ADR 0007 for the terms and decision record. Official pages checked on
2026-10-03: [pricing](https://www.geoapify.com/pricing/),
[map tiles](https://apidocs.geoapify.com/docs/maps),
[geocoding](https://www.geoapify.com/geocoding-api/),
[routing](https://apidocs.geoapify.com/docs/routing/), and OpenStreetMap
[tile policy](https://operations.osmfoundation.org/policies/tiles/).

## Data and API contracts

Add a reversible migration for nullable source attribution fields on `places`:
source name, exact source attribution, license, and source URL. The existing
`provider`, `provider_place_id`, and owner/provider/id uniqueness constraint
identify imported records. Expose these fields in place responses and preserve
the exact attribution returned for each imported result. Re-import must not
replace manually edited fields or attribution already stored on that place.

Add typed contracts:

| Method | Route | Contract |
| --- | --- | --- |
| `GET` | `/v1/trips/{trip_id}/places/search?q=...&limit=...` | Validate the owner-scoped trip; submit a bounded query to Geoapify; return normalized results with external id, name, address, category, and coordinates. Bias to known trip coordinates when available. Search does not write travel state. |
| `POST` | `/v1/trips/{trip_id}/saved-places/import` | Accept one selected normalized Geoapify result and optional note; validate fields/coordinate pair; owner-scope and transactionally upsert by provider identity plus create/reuse the trip's saved-place relationship. Return the saved-place response. Never overwrite existing place edits. |
| `POST` | `/v1/trips/{trip_id}/logistics/estimate` | Accept `day_id`, `mode` (`walk`, `drive`, `bicycle`, `transit`), and `buffer_minutes` (default 15, bounded 0–120). Return generated time and per-leg item ids, duration, distance, route-line coordinates, available time gap, and warning flag. Do not persist results. |

For logistics, inspect consecutive items in `sort_order` on the requested day.
Estimate a leg only if both items are active, both have coordinates (from their
place or linked reservation place), the first has an end time, and the next
has a start time. Date-only, untimed, cancelled, coordinate-free, and
non-consecutive items do not form route legs. Compare trip-local aware instants
after the existing timezone conversion. Flag a warning when
`available_gap_seconds < route_duration_seconds + buffer_minutes * 60`.
Return the contributing values so the message can explain the warning. A
provider failure must produce the shared safe API error envelope and leave all
travel state unchanged.

The provider client has a bounded timeout, validates upstream response shape,
and translates unavailable/configuration/invalid-response conditions into
stable domain/API errors. Do not expose the server API key in responses or
logs. Do not send itinerary notes, reservation references, names, or
confirmation data to routing; only coordinate pairs and selected mode leave
the app. Place search sends only the user's query and an optional coarse
coordinate bias.

## Frontend behavior

- Replace the disabled Map navigation entry with a working map anchor.
- Render a responsive, client-only XYZ tile map. Include markers for places
  attached to selected-day items and reservations, plus trip-saved candidates;
  avoid duplicate markers when one place is used by several records. Provide a
  corresponding accessible list so the map is not the only way to inspect
  locations. Show an empty state when no trip places have coordinates.
- Search only after form submission. Display up to ten results with attribution
  and an explicit “Save candidate” action. Preserve typed values after
  provider/API errors. Imported records appear in existing places/candidates
  after refresh and can be linked through the existing item editor.
- Add a selected-day and travel-mode selector, transfer-buffer control, and an
  explicit estimate button. Show generated-at/provider context, route legs,
  distance, travel duration, available gap, and clear warning details. Draw
  returned route geometry on the map for the selected day. Do not run routing
  on ordinary page loads or after every CRUD refresh.
- When the public tile key is missing, keep search/logistics controls' own
  server errors understandable and render an explanatory map placeholder. The
  rest of the trip workspace stays operational.
- Keep all existing manual place create/edit, trip, itinerary, reservation,
  conflict, and saved-place flows intact.

## Work packages and commit boundaries

### P3.1 — Provider boundary and typed contracts

- Add Geoapify settings with bounded request timeout and server-side key.
- Implement a typed async Geoapify client for submitted geocoding search and
  routing, including response parsing and provider error mapping.
- Add normalized search/import/logistics request and response schemas, exposing
  provider source fields on place representations.
- Add unit coverage with mocked HTTP responses and no real credentials.

### P3.2 — Owner-scoped search, idempotent import, and logistics services

- Add trip-scoped route adapters, checking the configured owner before calling
  providers.
- Reuse existing place/saved-place services and trip transaction boundaries.
- Make provider-result import idempotent and avoid overwriting user-authored
  fields or returning another owner's matching provider record. Recover from
  the unique-key race when concurrent imports to different trips select the
  same provider place.
- Implement pure eligibility and warning calculation around the provider
  response; include route estimate in response only.
- Cover cross-owner/trip cases, concurrent cross-trip import, malformed
  provider data, empty/missing values, and deterministic warning boundaries.

### P3.3 — Map, search, and logistics UI

- Add an in-repository XYZ tile component with pointer panning, bounded zoom,
  wrapped world coordinates, markers, route geometry, map attribution, and a
  screen-reader-accessible location list. Use only the configured Geoapify
  tile key; do not add a package dependency for the map.
- Add the map, accessible location list, search/import flow, day and mode
  controls, logistics summary/warnings, route polylines, attribution, and
  graceful missing-key states.
- Extend the typed API client and clear only stale logistics output when the
  underlying trip changes. Disable day/mode/buffer controls while an estimate
  is pending so the response cannot be displayed under changed inputs.
- Add focused map-geometry and contract checks without external API keys.
  UI integration is verified with ESLint, TypeScript, and a production build;
  do not introduce a component test framework solely for this small workspace.

### P3.4 — Docs and release record

- Update README product status, data model provider behavior, local env/config
  instructions, handoff, and roadmap to distinguish Phase 3 delivered behavior
  from later AI/cloud work.
- Record commits, actual checks and credentials/database limitations in
  `docs/releases/phase-3-maps-logistics.md`.

Commit P3.1–P3.2 together as one backend provider/API feature slice; commit
P3.3 separately as the map/logistics workspace; commit P3.4 as release/docs
evidence. Do not create one commit per checklist item.

## Acceptance criteria

- Search, import, maps, and routing are limited to the configured owner and
  trip; a missing/foreign trip is indistinguishable from not found.
- Search is a user-submitted query of at least two trimmed characters, has a
  bounded result count, does not write state, and does not issue autocomplete
  calls.
- A selected provider result is imported only after explicit user action.
  Re-import is deterministic, preserves edits, and cannot create duplicate
  saved-place relationships, including concurrent imports of one provider
  place into different trips owned by the same user.
- A map displays every located trip item/reservation/candidate in the selected
  view, has usable mobile dimensions and visible attribution, and provides an
  equivalent accessible location list.
- Logistics estimates use the requested mode and consecutive eligible
  schedule pairs, return geometry and units, and flag precisely when the
  available gap is below route duration plus buffer. Missing data is skipped
  without a false warning.
- Search and route data are not silently persisted, and no provider call
  changes the authoritative itinerary or booking state.
- Provider/config/network errors are recoverable; manual CRUD still works
  without provider keys.
- No direct browser-to-provider search/routing calls, personal AI package
  imports, authentication, cloud deployment, PostGIS, or external booking
  imports are added.
- Local setup and release docs explain the two Geoapify key locations,
  attribution, current quota implications, and checks run.

## Verification

Use provider mocks for backend contracts; do not require a Geoapify account for
tests. Automated checks cover supported Geoapify JSON mapping, missing-key and
upstream error behavior, contract bounds, route eligibility, warning
boundaries, provider-import idempotence and edit preservation (PostgreSQL test),
cross-trip concurrent provider-import recovery (PostgreSQL test), and map
projection/date-line/tile-wrapping math. Verify map empty/missing-key rendering
and source-attribution presentation through code review, ESLint, TypeScript,
and production build. No component-test runner is part of the frontend
toolchain.

Run the applicable repository commands:

```bash
make backend-test
make backend-lint
make backend-typecheck
make frontend-check
cd frontend && corepack pnpm build
```

Migration checks should confirm that the new attribution columns apply and
revert cleanly, and that a clean Phase 2 database upgrades to head.
