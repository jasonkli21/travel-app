import assert from "node:assert/strict";
import test from "node:test";

import { travelApi } from "../lib/api.ts";

test("workspace settings, place search/import, and itinerary moves keep revisions and request order", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push({ url, method: options.method ?? "GET", headers: new Headers(options.headers), body: options.body });
    return Response.json({ ok: true });
  });

  await travelApi.updateTrip("trip-1", {
    title: "Paris",
    start_date: "2026-10-01",
    end_date: "2026-10-04",
    timezone: "Europe/Paris",
  }, 7);
  await travelApi.searchPlaces("trip-1", "Paris & Lyon");
  await travelApi.importPlace("trip-1", {
    provider_place_id: "provider-1",
    name: "Cafe",
    address: "Paris",
    category: "cafe",
    latitude: 48.85,
    longitude: 2.35,
    provider_source_name: "Geoapify",
    provider_source_attribution: "© OpenStreetMap contributors",
    provider_source_license: "ODbL 1.0",
    provider_source_url: "https://example.test/source",
    note: null,
  }, 8);
  await travelApi.moveItem("trip-1", "item-1", "day-2", 3, 9);
  await travelApi.estimateLogistics("trip-1", {
    day_id: "day-2",
    mode: "transit",
    buffer_minutes: 15,
  });

  assert.deepEqual(calls.map(({ url, method }) => [method, url]), [
    ["PATCH", "/api/v1/trips/trip-1"],
    ["GET", "/api/v1/trips/trip-1/places/search?q=Paris%20%26%20Lyon&limit=10"],
    ["POST", "/api/v1/trips/trip-1/saved-places/import"],
    ["POST", "/api/v1/trips/trip-1/items/item-1/move"],
    ["POST", "/api/v1/trips/trip-1/logistics/estimate"],
  ]);
  assert.equal(calls[0].headers.get("x-expected-revision"), "7");
  assert.deepEqual(JSON.parse(calls[0].body), {
    title: "Paris",
    start_date: "2026-10-01",
    end_date: "2026-10-04",
    timezone: "Europe/Paris",
  });
  assert.equal(calls[1].headers.has("x-expected-revision"), false);
  assert.equal(calls[2].headers.get("x-expected-revision"), "8");
  assert.deepEqual(JSON.parse(calls[2].body), {
    provider_place_id: "provider-1",
    name: "Cafe",
    address: "Paris",
    category: "cafe",
    latitude: 48.85,
    longitude: 2.35,
    provider_source_name: "Geoapify",
    provider_source_attribution: "© OpenStreetMap contributors",
    provider_source_license: "ODbL 1.0",
    provider_source_url: "https://example.test/source",
    note: null,
  });
  assert.equal(calls[3].headers.get("x-expected-revision"), "9");
  assert.deepEqual(JSON.parse(calls[3].body), { destination_day_id: "day-2", position: 3 });
  assert.equal(calls[4].headers.has("x-expected-revision"), false);
  assert.deepEqual(JSON.parse(calls[4].body), {
    day_id: "day-2",
    mode: "transit",
    buffer_minutes: 15,
  });
});
