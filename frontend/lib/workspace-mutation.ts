type WorkspaceMutationOptions = {
  canStart: () => boolean;
  start: () => void;
  operation: () => Promise<unknown>;
  refresh: () => Promise<boolean>;
  refreshAfter?: boolean;
  onRefreshFailure: () => void;
  onMutationError: (error: unknown) => void;
  finish: () => void;
};

export async function runWorkspaceMutation({
  canStart,
  start,
  operation,
  refresh,
  refreshAfter = true,
  onRefreshFailure,
  onMutationError,
  finish,
}: WorkspaceMutationOptions): Promise<boolean> {
  if (!canStart()) return false;

  start();
  try {
    await operation();
    if (refreshAfter && !await refresh()) onRefreshFailure();
    return true;
  } catch (error) {
    onMutationError(error);
    return false;
  } finally {
    finish();
  }
}

export function reloadWorkspaceSnapshot(
  refresh: (options: { resetDrafts: true }) => Promise<boolean>,
): Promise<boolean> {
  return refresh({ resetDrafts: true });
}
