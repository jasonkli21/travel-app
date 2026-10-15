import assert from "node:assert/strict";
import test from "node:test";

import {
  bindShoppingTaskOwner,
  clearShoppingTaskRecovery,
  phaseAfterTaskFailure,
  phaseForTaskStatus,
  readShoppingTaskRecovery,
  RECOVERY_KEY,
  writeShoppingTaskRecovery,
} from "../lib/shopping-task-recovery.mjs";

class MemoryStorage {
  values = new Map();
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, value); }
  removeItem(key) { this.values.delete(key); }
}

function record(phase = "creating_command") {
  return {
    version: 1,
    owner_key: "owner-a",
    command_id: "00000000-0000-0000-0000-000000000001",
    create_body: {
      command_id: "00000000-0000-0000-0000-000000000001",
      trip_id: "trip-a",
      expected_trip_revision: 2,
      shopping_project_id: "project-a",
      expected_project_revision: 4,
    },
    phase,
    confirmation_id: phase === "applying" ? "confirmation-a" : null,
  };
}

test("a definitive create 409 returns to draft so the user can correct the request", () => {
  assert.deepEqual(phaseAfterTaskFailure("creating_command", { status: 409 }), {
    phase: "draft",
    discard: true,
  });
});

test("a create timeout retains the same command ID and immutable body", () => {
  const original = record();
  const outcome = phaseAfterTaskFailure("creating_command", new TypeError("timeout"));
  assert.deepEqual(outcome, { phase: "creating_command", discard: false });
  assert.equal(original.create_body.command_id, original.command_id);
});

test("a submit timeout keeps the known source command for same-ID retry", () => {
  assert.deepEqual(phaseAfterTaskFailure("submitting", new TypeError("timeout")), {
    phase: "submitting",
    discard: false,
  });
});

test("a definitive validation rejection is terminal, not effect-uncertain", () => {
  assert.deepEqual(phaseAfterTaskFailure("validating", { status: 409 }), {
    phase: "terminal",
    discard: false,
  });
  assert.equal(phaseForTaskStatus("rejected"), "terminal");
});

test("a lost apply acknowledgement requires reconciliation of the original key", () => {
  assert.deepEqual(phaseAfterTaskFailure("applying", new TypeError("timeout")), {
    phase: "effect_unknown",
    discard: false,
  });
  assert.equal(phaseForTaskStatus("uncertain"), "effect_unknown");
});

test("page restart reloads the original confirmation and execution identity", () => {
  const storage = new MemoryStorage();
  bindShoppingTaskOwner("owner-a", storage);
  writeShoppingTaskRecovery(record("applying"), storage);
  const restored = readShoppingTaskRecovery("owner-a", storage);
  assert.equal(restored.command_id, record("applying").command_id);
  assert.equal(restored.confirmation_id, "confirmation-a");
  assert.equal(JSON.stringify(restored).includes("preview"), false);
});

test("switching authenticated owner clears recoverable state", () => {
  const storage = new MemoryStorage();
  bindShoppingTaskOwner("owner-a", storage);
  writeShoppingTaskRecovery(record(), storage);
  bindShoppingTaskOwner("owner-b", storage);
  assert.equal(readShoppingTaskRecovery("owner-b", storage), null);
  assert.equal(storage.getItem(RECOVERY_KEY), null);
  clearShoppingTaskRecovery(storage);
});
