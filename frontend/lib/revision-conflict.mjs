const MESSAGE =
  "This trip or place changed elsewhere. Reload the workspace to review the current data before editing again. Reloading clears open editors.";

export function revisionConflictRecovery(error) {
  if (error && typeof error === "object" && error.code === "stale_revision") {
    return { requiresReload: true, message: MESSAGE };
  }
  return null;
}
