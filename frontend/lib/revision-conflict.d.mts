export function revisionConflictRecovery(
  error: unknown,
): { requiresReload: true; message: string } | null;
