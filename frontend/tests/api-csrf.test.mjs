import assert from "node:assert/strict";
import test from "node:test";

import { travelApi } from "../lib/api.ts";
import { csrfCookieValue } from "../lib/api-request.mjs";
import { proxyRequest } from "../lib/proxy.mjs";

const csrf = "a".repeat(43);

test("typed trip writes carry the browser CSRF proof through the same-origin proxy", async (t) => {
  globalThis.document = { cookie: `__Host-travel_csrf=${csrf}` };
  t.after(() => { delete globalThis.document; });
  const seen = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    const browserUrl = new URL(url, "http://localhost:3000");
    const headers = new Headers(options.headers);
    headers.set("host", "localhost:3000");
    headers.set("origin", "http://localhost:3000");
    headers.set("cookie", `__Host-travel_session=opaque; __Host-travel_csrf=${csrf}`);
    const browserRequest = new Request(browserUrl, { ...options, headers });
    const path = browserUrl.pathname.replace(/^\/api\/v1\//, "").split("/");
    return proxyRequest(browserRequest, path, {
      fetchImpl: async (_upstream, init) => {
        seen.push({ method: init.method, csrf: init.headers.get("x-csrf-token"), cookie: init.headers.get("cookie") });
        return init.method === "DELETE" ? new Response(null, { status: 204 }) : Response.json({ id: "trip-1", revision: 0 });
      },
    });
  });
  await travelApi.createTrip({ title: "Synthetic", start_date: "2026-01-01", end_date: "2026-01-01", timezone: "UTC" });
  await travelApi.deleteTrip("trip-1", 0);
  assert.deepEqual(seen.map(({ method, csrf: token }) => [method, token]), [["POST", csrf], ["DELETE", csrf]]);
  assert.ok(seen.every(({ cookie }) => cookie.includes("__Host-travel_session=opaque")));
});

test("duplicate or malformed CSRF cookies are never chosen", () => {
  assert.equal(csrfCookieValue(`__Host-travel_csrf=${csrf}; __Host-travel_csrf=${csrf}`), null);
  assert.equal(csrfCookieValue("__Host-travel_csrf=%0a"), null);
  assert.equal(csrfCookieValue(`x=1; __Host-travel_csrf=${csrf}`), csrf);
});
