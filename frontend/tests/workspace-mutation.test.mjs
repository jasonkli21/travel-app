import assert from "node:assert/strict";
import test from "node:test";

import { uncertainMutationError } from "../lib/mutation-outcome.mjs";
import { reloadWorkspaceSnapshot, runWorkspaceMutation } from "../lib/workspace-mutation.ts";

function createHarness({ operation, refresh }) {
  let busy = false;
  let stale = false;
  let openDraft = true;
  let error = null;
  let mutationCount = 0;

  async function run() {
    return runWorkspaceMutation({
      canStart: () => !busy && !stale,
      start: () => { busy = true; },
      operation: async () => {
        mutationCount += 1;
        await operation();
      },
      refresh,
      onRefreshFailure: () => {
        stale = true;
        error = "Saved, but workspace reload is required.";
      },
      onMutationError: (nextError) => {
        if (uncertainMutationError(nextError)) stale = true;
        error = nextError.message;
      },
      finish: () => { busy = false; },
    });
  }

  return {
    async submit() {
      if (!openDraft) return false;
      const saved = await run();
      if (saved) openDraft = false;
      return saved;
    },
    async reload() {
      await reloadWorkspaceSnapshot(async (options) => {
        assert.equal(options.resetDrafts, true);
        openDraft = false;
        stale = false;
      });
    },
    state: () => ({ busy, stale, openDraft, error, mutationCount }),
  };
}

test("committed create closes its draft after refresh fails and cannot submit twice", async () => {
  const harness = createHarness({
    operation: async () => {},
    refresh: async () => false,
  });

  assert.equal(await harness.submit(), true);
  assert.deepEqual(harness.state(), {
    busy: false,
    stale: true,
    openDraft: false,
    error: "Saved, but workspace reload is required.",
    mutationCount: 1,
  });
  assert.equal(await harness.submit(), false);
  assert.equal(harness.state().mutationCount, 1);
});

test("uncertain write blocks retries until explicit reload clears the draft", async () => {
  let refreshCount = 0;
  const harness = createHarness({
    operation: async () => {
      throw Object.assign(new Error("response was lost"), { status: 500 });
    },
    refresh: async () => { refreshCount += 1; return true; },
  });

  assert.equal(await harness.submit(), false);
  assert.deepEqual(harness.state(), {
    busy: false,
    stale: true,
    openDraft: true,
    error: "response was lost",
    mutationCount: 1,
  });
  assert.equal(await harness.submit(), false);
  assert.equal(harness.state().mutationCount, 1);
  assert.equal(refreshCount, 0, "uncertain writes require an explicit workspace reload");

  await harness.reload();
  assert.equal(harness.state().stale, false);
  assert.equal(harness.state().openDraft, false);
});
