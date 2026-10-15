export const RECOVERY_KEY = "travel-shopping-task-recovery-v1";
const OWNER_KEY = "travel-shopping-task-owner-v1";

export function bindShoppingTaskOwner(ownerKey, storage = sessionStorage) {
  const previous = storage.getItem(OWNER_KEY);
  if (!ownerKey || (previous && previous !== ownerKey)) {
    storage.removeItem(RECOVERY_KEY);
  }
  if (ownerKey) storage.setItem(OWNER_KEY, ownerKey);
  else storage.removeItem(OWNER_KEY);
}

export function readShoppingTaskRecovery(ownerKey, storage = sessionStorage) {
  if (!ownerKey) return null;
  const raw = storage.getItem(RECOVERY_KEY);
  if (!raw) return null;
  try {
    const record = JSON.parse(raw);
    if (
      record?.version === 1 &&
      record.owner_key === ownerKey &&
      typeof record.command_id === "string" &&
      typeof record.create_body === "object" &&
      typeof record.phase === "string"
    ) {
      return record;
    }
  } catch {
    // Invalid recoverable state is discarded; the server remains authoritative.
  }
  storage.removeItem(RECOVERY_KEY);
  return null;
}

export function writeShoppingTaskRecovery(record, storage = sessionStorage) {
  storage.setItem(RECOVERY_KEY, JSON.stringify(record));
}

export function clearShoppingTaskRecovery(storage = sessionStorage) {
  storage.removeItem(RECOVERY_KEY);
  storage.removeItem(OWNER_KEY);
}

export function phaseAfterTaskFailure(phase, error) {
  const status = error?.status;
  const definitive = Number.isInteger(status) && status >= 400 && status < 500;
  if (phase === "creating_command") {
    return { phase: definitive ? "draft" : "creating_command", discard: definitive };
  }
  if (phase === "submitting") {
    return { phase: definitive ? "terminal" : "submitting", discard: false };
  }
  if (phase === "applying") {
    return { phase: definitive ? "terminal" : "effect_unknown", discard: false };
  }
  if (phase === "validating" || phase === "confirming") {
    return { phase: definitive ? "terminal" : phase, discard: false };
  }
  return { phase, discard: false };
}

export function phaseForTaskStatus(state) {
  if (["proposed", "validated", "confirmed"].includes(state)) return state;
  if (["executing", "reconciling", "uncertain"].includes(state)) return "effect_unknown";
  return "terminal";
}
