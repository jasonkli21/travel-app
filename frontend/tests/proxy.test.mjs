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

test("proxy forwards only the explicitly supported revision precondition", async () => {
  let forwarded;
  const fetchImpl = async (_upstream, options) => {
    forwarded = options.headers;
    return Response.json({ ok: true });
  };
  await proxyRequest(new Request(url, {
    method: "PATCH",
    headers: {
      host: "localhost:3000",
      origin: "http://localhost:3000",
      "content-type": "application/json",
      "x-expected-revision": "12",
      cookie: "session=private",
      authorization: "Bearer private",
      "x-arbitrary-client-header": "do-not-forward",
    },
    body: JSON.stringify({ title: "New title" }),
  }), ["trips", "trip-id"], { fetchImpl });

  assert.deepEqual([...forwarded.keys()].sort(), ["content-type", "origin", "x-expected-revision"]);
  assert.equal(forwarded.get("x-expected-revision"), "12");

  forwarded = null;
  await proxyRequest(new Request(url, { method: "DELETE" }), ["trips", "trip-id"], { fetchImpl });
  assert.equal(forwarded.has("x-expected-revision"), false);
});

test("proxy forwards only session cookies and derives the AI user token from its HttpOnly cookie", async () => {
  let forwarded;
  const fetchImpl = async (_upstream, options) => {
    forwarded = options.headers;
    return Response.json({ ok: true });
  };
  const request = new Request("http://localhost:3000/api/v1/trips/trip-id/research", {
    method: "POST",
    headers: {
      host: "localhost:3000",
      origin: "http://localhost:3000",
      cookie: [
        "__Host-travel_session=opaque-session",
        "__Host-travel_csrf=csrf-proof",
        "__Host-travel_ai_token=signed-google-user-token",
        "session=untrusted",
      ].join("; "),
      "x-csrf-token": "csrf-proof",
      "x-user-id-token": "attacker-supplied-header",
      authorization: "Bearer attacker-service-token",
      "x-owner-id": "attacker-owner",
    },
    body: "{}",
  });
  await proxyRequest(request, ["trips", "trip-id", "research"], { fetchImpl });

  assert.equal(
    forwarded.get("cookie"),
    "__Host-travel_session=opaque-session; __Host-travel_csrf=csrf-proof",
  );
  assert.equal(forwarded.get("x-user-id-token"), "signed-google-user-token");
  assert.equal(forwarded.get("authorization"), null);
  assert.equal(forwarded.get("x-owner-id"), null);
  assert.equal(forwarded.get("origin"), "http://localhost:3000");
  assert.equal(forwarded.get("x-csrf-token"), "csrf-proof");
});

test("proxy forwards only session cookies returned by the backend", async () => {
  const fetchImpl = async () => {
    const headers = new Headers({ "content-type": "application/json" });
    headers.append("set-cookie", "__Host-travel_session=opaque; Secure; HttpOnly; Path=/; SameSite=Lax");
    headers.append("set-cookie", "__Host-travel_csrf=csrf; Secure; Path=/; SameSite=Strict");
    headers.append("set-cookie", "untrusted=private; Path=/");
    return new Response("{}", { headers });
  };
  const response = await proxyRequest(new Request(url), ["auth", "session"], { fetchImpl });
  const cookies = response.headers.getSetCookie();
  assert.equal(cookies.length, 2);
  assert.ok(cookies.some((cookie) => cookie.startsWith("__Host-travel_session=")));
  assert.ok(cookies.some((cookie) => cookie.startsWith("__Host-travel_csrf=")));
  assert.ok(cookies.every((cookie) => !cookie.startsWith("untrusted=")));
});

test("proxy adds the AI user token only for exact capability path segments", async () => {
  let forwarded;
  const fetchImpl = async (_upstream, options) => {
    forwarded = options.headers;
    return Response.json({ ok: true });
  };
  const request = new Request(url, {
    headers: { cookie: "__Host-travel_ai_token=signed-user-token" },
  });
  await proxyRequest(request, ["trips", "trip-id", "research-old"], { fetchImpl });
  assert.equal(forwarded.get("x-user-id-token"), null);
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

test("private source upload streams beyond the ordinary JSON cap", async () => {
  const bytes = new Uint8Array(70 * 1024).fill(97);
  const input = new ReadableStream({
    start(controller) { controller.enqueue(bytes); controller.close(); },
  });
  let seenHeaders;
  let size = 0;
  const request = new Request(
    "http://localhost:3000/api/v1/trips/00000000-0000-4000-8000-000000000001/imports",
    {
      method: "POST",
      headers: {
        host: "localhost:3000",
        origin: "http://localhost:3000",
        cookie: "__Host-travel_session=session; __Host-travel_csrf=csrf",
        "x-csrf-token": "csrf",
        "x-import-request-key": "upload_0001",
        "x-source-filename": "booking.txt",
        "content-type": "text/plain",
      },
      body: input,
      duplex: "half",
    },
  );
  const response = await proxyRequest(request, [
    "trips", "00000000-0000-4000-8000-000000000001", "imports",
  ], {
    fetchImpl: async (_url, options) => {
      seenHeaders = options.headers;
      assert.equal(options.duplex, "half");
      const reader = options.body.getReader();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        size += value.byteLength;
      }
      return Response.json({ size });
    },
  });
  assert.equal(response.status, 200);
  assert.equal(size, bytes.byteLength);
  assert.equal(seenHeaders.get("x-import-request-key"), "upload_0001");
  assert.equal(seenHeaders.get("x-source-filename"), "booking.txt");
  assert.equal(seenHeaders.get("cookie"), "__Host-travel_session=session; __Host-travel_csrf=csrf");
});

test("private source upload enforces its own byte cap and requires an idempotency key", async () => {
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(512 * 1024));
      controller.enqueue(new Uint8Array(512 * 1024 + 1));
      controller.close();
    },
  });
  const request = new Request(
    "http://localhost:3000/api/v1/trips/00000000-0000-4000-8000-000000000001/imports",
    {
      method: "POST",
      headers: {
        host: "localhost:3000",
        origin: "http://localhost:3000",
        "x-import-request-key": "upload_0002",
        "content-type": "text/plain",
      },
      body,
      duplex: "half",
    },
  );
  const path = ["trips", "00000000-0000-4000-8000-000000000001", "imports"];
  const response = await proxyRequest(request, path, {
    fetchImpl: async (_url, options) => {
      const reader = options.body.getReader();
      while (true) {
        const { done } = await reader.read();
        if (done) break;
      }
      return Response.json({ ok: true });
    },
  });
  assert.equal(response.status, 413);

  const missingKey = new Request(
    "http://localhost:3000/api/v1/trips/00000000-0000-4000-8000-000000000001/imports",
    { method: "POST", headers: { "content-type": "text/plain" }, body: "private" },
  );
  assert.equal((await proxyRequest(missingKey, path, {
    fetchImpl: () => { throw new Error("must not read an unkeyed upload"); },
  })).status, 400);
});

test("private source download preserves binary bytes and inert response headers", async () => {
  const bytes = new Uint8Array([0, 255, 10, 13, 128]);
  const request = new Request("http://localhost:3000/api/v1/trips/source");
  const response = await proxyRequest(request, [
    "trips", "00000000-0000-4000-8000-000000000001", "imports",
    "00000000-0000-4000-8000-000000000002", "source",
  ], {
    fetchImpl: async () => new Response(bytes, {
      headers: {
        "content-type": "application/pdf",
        "content-disposition": "attachment; filename=source",
        "x-content-type-options": "nosniff",
      },
    }),
  });
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()), bytes);
  assert.equal(response.headers.get("content-disposition"), "attachment; filename=source");
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
});
