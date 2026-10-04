import assert from "node:assert/strict";
import test from "node:test";

import { request } from "../lib/api-request.mjs";
import { revisionConflictRecovery } from "../lib/revision-conflict.mjs";

test("stale revision conflicts block edits and direct the traveler to reload", () => {
  const recovery = revisionConflictRecovery({
    code: "stale_revision",
    status: 409,
    message: "The trip changed after it was loaded.",
  });

  assert.deepEqual(recovery, {
    requiresReload: true,
    message:
      "This trip or place changed elsewhere. Reload the workspace to review the current data before editing again.",
  });
});

test("ordinary validation conflicts remain editable", () => {
  assert.equal(revisionConflictRecovery({ code: "trip_days_contain_items", status: 409 }), null);
  assert.equal(revisionConflictRecovery(new Error("network failure")), null);
});

test("an actual stale API response activates reload recovery with its precondition", async (t) => {
  const fetchMock = t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "/api/v1/trips/trip-1");
    assert.equal(options.headers["X-Expected-Revision"], "4");
    return Response.json(
      {
        error: {
          message: "This trip changed after it was loaded.",
          code: "stale_revision",
          details: { aggregate: "trip", expected_revision: 4, current_revision: 5 },
        },
      },
      { status: 409 },
    );
  });

  await assert.rejects(
    request("/trips/trip-1", {
      method: "PATCH",
      headers: { "X-Expected-Revision": "4" },
      body: JSON.stringify({ title: "Changed title" }),
    }),
    (error) => {
      assert.equal(error.code, "stale_revision");
      assert.equal(error.status, 409);
      assert.deepEqual(revisionConflictRecovery(error), {
        requiresReload: true,
        message:
          "This trip or place changed elsewhere. Reload the workspace to review the current data before editing again.",
      });
      return true;
    },
  );
  assert.equal(fetchMock.mock.callCount(), 1);
});
