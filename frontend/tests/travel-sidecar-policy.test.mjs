import assert from "node:assert/strict";
import test from "node:test";

import { travelSidecarActions } from "../lib/api.ts";

test("Travel sidecar actions follow the shared application policy", () => {
  assert.deepEqual(travelSidecarActions(null), { copy: false, draft: false });
  assert.deepEqual(travelSidecarActions({
    copy_allowed: false,
    draft_insert_target_ids: [],
  }), { copy: false, draft: false });
  assert.deepEqual(travelSidecarActions({
    copy_allowed: true,
    draft_insert_target_ids: ["other.message_composer"],
  }), { copy: true, draft: false });
  assert.deepEqual(travelSidecarActions({
    copy_allowed: false,
    draft_insert_target_ids: ["travel.message_composer"],
  }), { copy: false, draft: true });
});
