# ADR 0007 — Geoapify maps, place search, and logistics

Status: accepted for Phase 3 implementation
Date: 2026-10-03

## Context

Phase 3 needs an interactive map, provider-backed place search/import, and
basic route-time estimates. The travel application already stores optional
place coordinates and external provider identity. It should keep travel state
authoritative, avoid a spatial database dependency, and make provider-derived
data distinguishable from manual edits.

The provider must support place-data retention after a user chooses to import a
result, provide clear attribution requirements, and have a free plan suitable
for a personal local-first application. Public OpenStreetMap Foundation tile
and Nominatim endpoints are community services with strict usage limits and no
availability guarantee, so they are not the application's runtime provider.

## Decision

- Use **Geoapify** for map tiles, submitted free-text place/address search, and
  road/walking/bicycle/transit route estimates. Keep requests behind the
  travel API except map-tile requests, which the browser must make to render
  the interactive map.
- Use an in-repository, client-only XYZ tile renderer with Geoapify's
  `osm-carto` raster tile style. Keep the map component dependency-free and
  limit its behavior to tile pan/zoom, markers, and route lines.
- Configure a server-only `GEOAPIFY_API_KEY` for search and routing. Configure
  a separate browser-visible `NEXT_PUBLIC_GEOAPIFY_API_KEY` for map tiles;
  restrict keys by API and localhost/deployed HTTP origin in Geoapify. Never
  commit real keys.
- Require the map to show `© OpenStreetMap contributors` and `Powered by
  Geoapify` attribution. Place results and imported place records retain and
  display Geoapify/OpenStreetMap source attribution. Keep attribution visible
  when data is shown away from the map.
- Search is submitted by the user, bounded to a short result list, and does not
  issue a request on each keystroke. A result is not added to travel state
  until the user explicitly imports it. Import is idempotent by the existing
  `(owner_id, provider, provider_place_id)` uniqueness constraint; importing a
  known result reuses its existing place and trip candidate relationship
  rather than overwriting user edits.
- Store provider identity in the existing `places.provider` and
  `places.provider_place_id` columns. Add nullable source name, exact source
  attribution, license, and source URL columns so attribution remains available
  away from the search response. Preserve existing manual metadata and stored
  attribution on repeat imports.
- Use Geoapify's Routing API for estimates between consecutive itinerary
  items on one day. Support `walk`, `drive`, `bicycle`, and `transit` modes.
  Include a configurable transfer buffer in the deterministic warning rule.
  Estimates and their geometry are fetched on explicit user action and returned
  to the UI only; do not persist them as permanent travel facts or call routing
  during ordinary trip reads.
- Treat provider estimates as advisory. Do not mutate itinerary items or
  reservations, assume live traffic or transit schedules, or prevent the user
  from keeping a tight schedule. Missing coordinates/times produce no warning.
- Do not introduce PostGIS, offline tile downloads, automatic geocoding of
  existing manual places, live opening hours, AI research, or cloud deployment.

## Provider review and operating limits

The provider comparison and terms were checked against the official
[Geoapify pricing page](https://www.geoapify.com/pricing/),
[map-tile documentation](https://apidocs.geoapify.com/docs/maps),
[geocoding documentation](https://www.geoapify.com/geocoding-api/),
[routing documentation](https://apidocs.geoapify.com/docs/routing/), and the
OpenStreetMap Foundation's [tile usage policy](https://operations.osmfoundation.org/policies/tiles/)
and [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/)
on 2026-10-03. Geoapify's Free plan is
listed as 3,000 credits per day, no credit card required, and up to five
requests per second. Current published examples price map tile requests at
0.25 credits each (about 3.5 credits for a typical 14-tile map view),
geocoding at one credit per request, and routing at one credit per waypoint
pair plus distance surcharges for each 500 km. Place-result storage is allowed
under Geoapify terms, with data-source attribution retained. The free quota is
soft and does not provide a contractual availability SLA; provider calls must
fail gracefully and manual planning must continue to work.

These limits and terms can change. Recheck provider documentation before any
cloud/public launch or material increase in usage. The key is a usage-control
boundary, not a promise of zero cost. Do not send confirmation codes, notes,
or other private itinerary details to the provider; requests contain only the
search string or coordinates needed for the requested operation.

## Consequences

- Phase 3 requires the user to obtain and configure free Geoapify keys before
  map tiles and provider-backed operations work. The rest of the local
  planner remains usable without them.
- Geoapify/OpenStreetMap attribution appears in the map, search results, and
  stored provider-place context.
- Provider estimates are fresh at request time but can be unavailable or
  inaccurate. They are never confused with durable user-authored schedules.
- Provider behavior is isolated behind one typed HTTP client so a later ADR
  can replace it without changing travel-domain storage or frontend API
  contracts.
