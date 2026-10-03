import assert from "node:assert/strict";
import test from "node:test";
import { ApiError, request } from "../lib/api-request.mjs";
import { uncertainMutationError } from "../lib/mutation-outcome.mjs";

test("lost write responses require reconciliation while validation failures allow correction", () => {
  for (const error of [new TypeError("fetch failed"), new SyntaxError("invalid success body"), { status: 500 }, { status: 503 }, { status: 504 }]) {
    assert.equal(uncertainMutationError(error), true);
  }
  for (const status of [400, 401, 403, 404, 409, 413, 422]) {
    assert.equal(uncertainMutationError({ status }), false);
  }
});

test("the actual client preserves rejection status for mutation recovery", async (t) => {
  for (const status of [422, 503]) {
    const fetchMock = t.mock.method(globalThis, "fetch", async () => Response.json({
      error: { message: "Safe failure", code: "failed", details: null },
    }, { status }));
    await assert.rejects(request("/trips", { method: "POST", body: "{}" }), (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, status);
      assert.equal(uncertainMutationError(error), status >= 500);
      return true;
    });
    fetchMock.mock.restore();
  }
});

test("lost or malformed success responses stay uncertain and 204 remains successful", async (t) => {
  const fetchMock = t.mock.method(globalThis, "fetch", async () => { throw new Error("lost"); });
  await assert.rejects(request("/trips", { method: "POST" }), (error) => uncertainMutationError(error));
  fetchMock.mock.mockImplementation(async () => new Response("broken", { status: 201 }));
  await assert.rejects(request("/trips", { method: "POST" }), (error) => uncertainMutationError(error));
  fetchMock.mock.mockImplementation(async () => new Response(null, { status: 204 }));
  assert.equal(await request("/trips/id", { method: "DELETE" }), undefined);
});
