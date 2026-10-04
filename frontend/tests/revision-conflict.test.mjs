import assert from "node:assert/strict";
import test from "node:test";

import { travelApi } from "../lib/api.ts";
import { proxyRequest } from "../lib/proxy.mjs";
import { revisionConflictRecovery } from "../lib/revision-conflict.mjs";

const recoveryMessage =
  "This trip or place changed elsewhere. Reload the workspace to review the current data before editing again. Reloading clears open editors.";

test("stale revision conflicts block edits and direct the traveler to reload", () => {
  const recovery = revisionConflictRecovery({
    code: "stale_revision",
    status: 409,
    message: "The trip changed after it was loaded.",
  });

  assert.deepEqual(recovery, { requiresReload: true, message: recoveryMessage });
});

test("ordinary validation conflicts remain editable", () => {
  assert.equal(revisionConflictRecovery({ code: "trip_days_contain_items", status: 409 }), null);
  assert.equal(revisionConflictRecovery(new Error("network failure")), null);
});

test("typed DELETE and shared-place writes carry revisions through the proxy to stale recovery", async (t) => {
  const calls = [];
  let stale = true;
  const fetchMock = t.mock.method(globalThis, "fetch", async (url, options) => {
    const browserUrl = new URL(url, "http://localhost:3000");
    const requestHeaders = new Headers(options.headers);
    requestHeaders.set("host", "localhost:3000");
    requestHeaders.set("origin", "http://localhost:3000");
    // Simulate ambient and caller-controlled browser headers. The proxy must
    // still forward only its explicit backend allowlist.
    requestHeaders.set("cookie", "session=private");
    requestHeaders.set("authorization", "Bearer private");
    requestHeaders.set("x-arbitrary-client-header", "private");
    const browserRequest = new Request(browserUrl, { ...options, headers: requestHeaders });
    const path = browserUrl.pathname.replace(/^\/api\/v1\//, "").split("/");
    return proxyRequest(browserRequest, path, {
      fetchImpl: async (upstream, proxyOptions) => {
        calls.push({ upstream, method: proxyOptions.method, headers: proxyOptions.headers });
        if (stale) {
          return Response.json({
            error: {
              message: "This trip or place changed after it was loaded.",
              code: "stale_revision",
              details: { aggregate: "trip", expected_revision: 4, current_revision: 5 },
            },
          }, { status: 409 });
        }
        return Response.json({ id: "place-1", revision: 0, name: "Cafe" });
      },
    });
  });

  await assert.rejects(travelApi.deleteItem("trip-1", "item-1", 4), (error) => {
    assert.equal(error.code, "stale_revision");
    assert.equal(error.status, 409);
    assert.deepEqual(revisionConflictRecovery(error), { requiresReload: true, message: recoveryMessage });
    return true;
  });
  assert.equal(calls[0].upstream, "http://localhost:8000/v1/trips/trip-1/items/item-1");
  assert.equal(calls[0].method, "DELETE");
  assert.equal(calls[0].headers.get("x-expected-revision"), "4");

  await assert.rejects(travelApi.updatePlace("place-1", { name: "Cafe updated" }, 8), (error) => {
    assert.equal(error.code, "stale_revision");
    assert.deepEqual(revisionConflictRecovery(error), { requiresReload: true, message: recoveryMessage });
    return true;
  });
  assert.equal(calls[1].upstream, "http://localhost:8000/v1/places/place-1");
  assert.equal(calls[1].method, "PATCH");
  assert.equal(calls[1].headers.get("x-expected-revision"), "8");

  for (const call of calls.slice(0, 2)) {
    const expectedHeaders = call.method === "DELETE"
      ? ["origin", "x-expected-revision"]
      : ["content-type", "origin", "x-expected-revision"];
    assert.deepEqual([...call.headers.keys()].sort(), expectedHeaders);
  }

  // A normal create-place request has no precondition and remains compatible
  // with the API's optional-header behavior.
  stale = false;
  await travelApi.createPlace({ name: "Cafe" });
  assert.equal(calls[2].method, "POST");
  assert.equal(calls[2].headers.has("x-expected-revision"), false);
  assert.deepEqual([...calls[2].headers.keys()].sort(), ["content-type", "origin"]);
  assert.equal(fetchMock.mock.callCount(), 3);
});
