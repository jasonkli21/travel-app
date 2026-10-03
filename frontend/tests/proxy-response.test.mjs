import assert from "node:assert/strict";
import test from "node:test";

import { readProxyBody } from "../lib/proxy-response.mjs";

test("successful no-content responses remain no-content", async () => {
  const response = await readProxyBody(new Response(null, { status: 204 }));
  const proxied = new Response(response, { status: 204 });

  assert.equal(response, null);
  assert.equal(proxied.status, 204);
  assert.equal(await proxied.text(), "");
});

test("successful JSON responses preserve their body", async () => {
  const response = await readProxyBody(
    new Response('{"ok":true}', {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );

  assert.equal(response, '{"ok":true}');
});
