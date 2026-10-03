import assert from "node:assert/strict";
import test from "node:test";
import { researchContextKey } from "../lib/research-context.mjs";
import { safeHttpUrl } from "../lib/urls.mjs";

test("research context invalidates changes in the projected itinerary", () => {
  const trip = {
    id: "trip", start_date: "2026-10-03", end_date: "2026-10-03", timezone: "UTC",
    days: [{ id: "day", date: "2026-10-03", title: "Downtown", items: [
      { id: "item", sort_order: 0, status: "planned", title: "Museum", start_time: "10:00", notes: "private" },
    ] }],
  };
  const key = researchContextKey(trip, "day");
  trip.days[0].items[0].notes = "new private note";
  assert.equal(researchContextKey(trip, "day"), key);
  trip.days[0].items[0].start_time = "11:00";
  assert.notEqual(researchContextKey(trip, "day"), key);
  trip.days = [];
  assert.notEqual(researchContextKey(trip, "day"), key);
});

test("source links accept only HTTP(S) links without credentials or controls", () => {
  for (const value of ["javascript:alert(1)", "https://u:p@example.test", "https://example.test/a\nb", "not a url"]) {
    assert.equal(safeHttpUrl(value), null);
  }
  assert.equal(safeHttpUrl("https://example.test/source"), "https://example.test/source");
});
