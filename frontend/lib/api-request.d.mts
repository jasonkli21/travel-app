export class ApiError extends Error {
  readonly code: string;
  readonly details: Record<string, unknown> | null;
  readonly status: number | null;
  constructor(message: string, code?: string, details?: Record<string, unknown> | null, status?: number | null);
}
export function request<T>(path: string, init?: RequestInit): Promise<T>;
export function download(
  path: string,
  init?: RequestInit,
): Promise<{
  blob: Blob;
  filename: string;
  tripRevision: string | null;
  generatedAt: string | null;
}>;
