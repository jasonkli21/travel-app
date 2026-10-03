import assert from "node:assert/strict";
import test from "node:test";
import { proxyRequest } from "../lib/proxy.mjs";

const url = "http://localhost:3000/api/v1/trips";

test("proxy validates the browser Host when Next uses an internal request URL", async () => {
  const fetchImpl = async () => new Response("{}", { headers: { "content-type": "application/json" } });
  const request = new Request(url, { method: "POST", headers: {
    host: "127.0.0.1:53049", origin: "http://127.0.0.1:53049",
  } });
  assert.equal((await proxyRequest(request, ["trips"], { fetchImpl })).status, 200);
  for (const host of ["evil.test", "localhost@evil.test", "localhost/path", "localhost?x", "localhost:bad"]) {
    assert.equal((await proxyRequest(new Request(url, { headers: { host } }), ["trips"], { fetchImpl })).status, 400);
  }
});

test("proxy rejects hostile origins, hosts and traversal before reaching the backend", async () => {
  const fetchImpl = () => { throw new Error("must not reach backend"); };
  for (const [request, path, status] of [
    [new Request(url, { method: "POST", headers: { origin: "https://evil.test" } }), ["trips"], 403],
    [new Request("http://evil.test/api/v1/trips"), ["trips"], 400],
    [new Request(url), ["..", "health"], 400],
    [new Request(url, { headers: { "sec-fetch-site": "cross-site" } }), ["trips"], 403],
  ]) {
    const response = await proxyRequest(request, path, { fetchImpl });
    assert.equal(response.status, status);
  }
});

test("proxy preserves JSON body, request ID and 204 deletion responses", async () => {
  let seen;
  const fetchImpl = async (upstream, options) => {
    seen = [upstream, options];
    return new Response(null, { status: 204, headers: { "x-request-id": "trace" } });
  };
  const response = await proxyRequest(new Request(url, {
    method: "DELETE", headers: { origin: "http://localhost:3000" }, body: "{}",
  }), ["trips", "trip-id"], { fetchImpl });
  assert.equal(seen[0], "http://localhost:8000/v1/trips/trip-id");
  assert.equal(new TextDecoder().decode(seen[1].body), "{}");
  assert.equal(seen[1].redirect, "error");
  assert.equal(response.status, 204);
  assert.equal(response.headers.get("x-request-id"), "trace");
  assert.equal(await response.text(), "");
});

test("proxy bounds chunked request bodies and reports backend failures", async () => {
  const request = new Request(url, { method: "POST", body: "x".repeat(65537) });
  assert.equal((await proxyRequest(request, ["trips"])).status, 413);
  const response = await proxyRequest(new Request(url), ["trips"], {
    fetchImpl: () => Promise.reject(new Error("PRIVATE UPSTREAM URL")),
  });
  assert.equal(response.status, 503);
  assert.ok(!(await response.text()).includes("PRIVATE"));
});
