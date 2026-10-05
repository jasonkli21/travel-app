import assert from "node:assert/strict";
import test from "node:test";

import {
  comparisonRetryKey,
  isAmbiguousComparisonFailure,
  parseComparisonRetry,
  readComparisonRetry,
  shouldKeepComparisonRequest,
  writeComparisonRetry,
} from "../lib/comparison-recovery.mjs";
import { uniqueComparisonPlaces } from "../lib/comparison-places.mjs";

const request = {
  category: "food",
  query: "sushi",
  reference_place_id: "00000000-0000-4000-8000-000000000001",
  reference_place_revision: 3,
  reference_latitude: 35.68,
  reference_longitude: 139.76,
  trip_revision: 7,
  radius_km: 5,
  max_results: 8,
  idempotency_key: "00000000-0000-4000-8000-000000000002",
};

test("the comparison radius default and supported choices satisfy browser/API bounds", () => {
  for (const radius_km of [0.5, 1, 5, 19.5, 20]) {
    assert.ok(parseComparisonRetry({ ...request, radius_km }));
  }
  for (const radius_km of [0, 0.1, 5.1, 20.5]) {
    assert.equal(parseComparisonRetry({ ...request, radius_km }), null);
  }
});

test("unknown outcomes keep one immutable request key across a page refresh", () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  writeComparisonRetry(storage, "trip-1", request);
  const restored = readComparisonRetry(storage, "trip-1");
  assert.deepEqual(restored, request);
  assert.equal(restored.reference_latitude, 35.68);
  assert.equal(restored.idempotency_key, request.idempotency_key);
  assert.equal(comparisonRetryKey("trip-1"), "travel-comparison-retry:trip-1");
  writeComparisonRetry(storage, "trip-1", null);
  assert.equal(readComparisonRetry(storage, "trip-1"), null);
});

test("only failures that can have an unknown provider outcome retain the request", () => {
  assert.equal(isAmbiguousComparisonFailure({ status: null }), true);
  assert.equal(isAmbiguousComparisonFailure(new Error("network")), true);
  assert.equal(isAmbiguousComparisonFailure({ status: 503 }), true);
  assert.equal(isAmbiguousComparisonFailure({ status: 503, code: "research_comparison_unknown" }), true);
  assert.equal(isAmbiguousComparisonFailure({ status: 503, code: "research_comparison_unavailable" }), false);
  assert.equal(isAmbiguousComparisonFailure({ status: 408 }), true);
  assert.equal(isAmbiguousComparisonFailure({ status: 429 }), true);
  assert.equal(isAmbiguousComparisonFailure({ status: 409 }), false);
  assert.equal(isAmbiguousComparisonFailure({ status: 422 }), false);
  assert.equal(shouldKeepComparisonRequest({ status: 409 }, false), false);
  assert.equal(shouldKeepComparisonRequest({ status: 409 }, true), true);
});

test("persisted retry data is rejected when it is malformed", () => {
  assert.equal(parseComparisonRetry({ ...request, category: "flight" }), null);
  assert.equal(parseComparisonRetry({ ...request, idempotency_key: "bad" }), null);
  assert.equal(parseComparisonRetry({ ...request, reference_longitude: 181 }), null);
});

test("reservation places can center comparisons and duplicate trip places appear once", () => {
  const hotel = { id: "hotel", name: "Hotel", latitude: 35, longitude: 139 };
  const unlocated = { id: "unlocated", name: "Unlocated", latitude: null, longitude: null };
  const trip = {
    days: [{ items: [{ place: hotel }] }],
  };
  const savedPlaces = [{ place: hotel }];
  const reservations = [
    { status: "confirmed", place: hotel },
    { status: "tentative", place: { id: "reservation-only", name: "Reservation only", latitude: 36, longitude: 140 } },
    { status: "confirmed", place: unlocated },
    { status: "cancelled", place: { id: "cancelled", name: "Cancelled", latitude: 37, longitude: 141 } },
  ];
  const places = uniqueComparisonPlaces(trip, savedPlaces, reservations);
  assert.deepEqual(places.map((place) => place.id), ["hotel", "reservation-only"]);
});

test("blocked sessionStorage access preserves the in-memory retry and logout clears it", async () => {
  const { browserComparisonStorage, clearComparisonRetries } = await import("../lib/comparison-recovery.mjs");
  const previousWindow = globalThis.window;
  globalThis.window = Object.defineProperty({}, "sessionStorage", {
    get() { throw new Error("storage denied"); },
  });
  try {
    assert.equal(browserComparisonStorage(), null);
    writeComparisonRetry(browserComparisonStorage(), "blocked-storage-trip", request);
    assert.deepEqual(readComparisonRetry(null, "blocked-storage-trip"), request);
    clearComparisonRetries(browserComparisonStorage());
    assert.equal(readComparisonRetry(null, "blocked-storage-trip"), null);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("logout clears persisted comparison queries without deleting unrelated browser state", async () => {
  const { clearComparisonRetries } = await import("../lib/comparison-recovery.mjs");
  const values = new Map([
    [comparisonRetryKey("stored-trip"), JSON.stringify(request)],
    ["unrelated-setting", "keep"],
  ]);
  const storage = {
    get length() { return values.size; },
    key: (index) => [...values.keys()][index] ?? null,
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  clearComparisonRetries(storage);
  assert.equal(values.has(comparisonRetryKey("stored-trip")), false);
  assert.equal(readComparisonRetry(storage, "stored-trip"), null);
  assert.equal(values.get("unrelated-setting"), "keep");
});
