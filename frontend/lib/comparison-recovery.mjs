const categories = new Set(["food", "activity", "neighborhood", "day_trip"]);
const resultLimits = new Set([4, 6, 8, 10]);
const listeners = new Map();
const memorySnapshots = new Map();

export function comparisonRetryKey(tripId) {
  return `travel-comparison-retry:${tripId}`;
}

export function isAmbiguousComparisonFailure(error) {
  if (error?.code === "research_comparison_unknown") return true;
  if (error?.code === "research_comparison_unavailable") return false;
  const status = error && typeof error.status === "number" ? error.status : null;
  return status === null || status >= 500 || status === 408 || status === 429;
}

export function shouldKeepComparisonRequest(error, reconcilingUnknownOutcome) {
  return reconcilingUnknownOutcome || isAmbiguousComparisonFailure(error);
}

export function parseComparisonRetry(value) {
  if (!value || typeof value !== "object") return null;
  const request = value;
  if (!categories.has(request.category)
    || typeof request.query !== "string" || !request.query.trim() || request.query.length > 180
    || typeof request.reference_place_id !== "string"
    || !Number.isInteger(request.reference_place_revision) || request.reference_place_revision < 0
    || !Number.isInteger(request.trip_revision) || request.trip_revision < 0
    || !Number.isFinite(request.reference_latitude) || request.reference_latitude < -90 || request.reference_latitude > 90
    || !Number.isFinite(request.reference_longitude) || request.reference_longitude < -180 || request.reference_longitude > 180
    || !Number.isFinite(request.radius_km) || request.radius_km < 0.5 || request.radius_km > 20
    || Math.abs((request.radius_km - 0.5) * 2 - Math.round((request.radius_km - 0.5) * 2)) > 1e-8
    || !resultLimits.has(request.max_results)
    || typeof request.idempotency_key !== "string"
    || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(request.idempotency_key)) {
    return null;
  }
  return {
    category: request.category,
    query: request.query,
    reference_place_id: request.reference_place_id,
    reference_place_revision: request.reference_place_revision,
    reference_latitude: request.reference_latitude,
    reference_longitude: request.reference_longitude,
    trip_revision: request.trip_revision,
    radius_km: request.radius_km,
    max_results: request.max_results,
    idempotency_key: request.idempotency_key,
  };
}

export function readComparisonRetry(storage, tripId) {
  try {
    const raw = getComparisonRetrySnapshot(storage, tripId);
    return raw === null ? null : parseComparisonRetry(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function getComparisonRetrySnapshot(storage, tripId) {
  const key = comparisonRetryKey(tripId);
  if (memorySnapshots.has(key)) return memorySnapshots.get(key);
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

export function subscribeComparisonRetry(tripId, listener) {
  const key = comparisonRetryKey(tripId);
  const callbacks = listeners.get(key) ?? new Set();
  callbacks.add(listener);
  listeners.set(key, callbacks);
  const onStorage = (event) => {
    if (event.key === key) listener();
  };
  if (typeof window !== "undefined") window.addEventListener("storage", onStorage);
  return () => {
    callbacks.delete(listener);
    if (callbacks.size === 0) listeners.delete(key);
    if (typeof window !== "undefined") window.removeEventListener("storage", onStorage);
  };
}

export function writeComparisonRetry(storage, tripId, request) {
  const key = comparisonRetryKey(tripId);
  const value = request === null ? null : JSON.stringify(request);
  memorySnapshots.set(key, value);
  try {
    if (request === null) storage.removeItem(key);
    else storage.setItem(key, value);
  } catch {
    // Keep this tab's exact key in memory when browser storage is unavailable.
  }
  for (const listener of listeners.get(key) ?? []) listener();
}
