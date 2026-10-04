import test from "node:test";
import assert from "node:assert/strict";

import {
  proposalCanApply,
  proposalIsTerminal,
  proposalPresentationState,
} from "../lib/proposal-state.mjs";

function proposal(overrides = {}) {
  return {
    state: "ready",
    lifecycle_state: "ready",
    base_trip_revision: 4,
    current_trip_revision: 4,
    expires_at: "2026-10-04T12:00:00Z",
    base_place_revisions: [{ place_id: "a", revision: 3 }],
    current_place_revisions: [{ place_id: "a", revision: 3 }],
    ...overrides,
  };
}

test("ready proposal requires a matching trip and shared-place footprint", () => {
  const value = proposal();
  assert.equal(proposalPresentationState(value, 4, Date.parse("2026-10-04T11:00:00Z")), "ready");
  assert.equal(proposalCanApply(value, 4, Date.parse("2026-10-04T11:00:00Z")), true);
  assert.equal(proposalPresentationState(value, 5), "stale");
  assert.equal(proposalCanApply(value, 5), false);
  assert.equal(proposalPresentationState(proposal({
    current_place_revisions: [{ place_id: "a", revision: 4 }],
  }), 4), "stale");
});

test("expired and terminal proposals cannot apply or re-enter the review flow", () => {
  const expired = proposal();
  assert.equal(proposalPresentationState(expired, 4, Date.parse("2026-10-04T13:00:00Z")), "expired");
  assert.equal(proposalCanApply(expired, 4, Date.parse("2026-10-04T13:00:00Z")), false);
  for (const state of ["failed", "applied", "rejected", "expired", "stale"]) {
    const terminal = proposal({ state, lifecycle_state: state === "stale" || state === "expired" ? "ready" : state });
    assert.equal(proposalIsTerminal(terminal, 4), true);
    assert.equal(proposalCanApply(terminal, 4), false);
  }
});
