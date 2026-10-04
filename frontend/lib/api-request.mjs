export class ApiError extends Error {
  constructor(message, code = "request_failed", details = null, status = null) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.details = details;
    this.status = status;
  }
}

export async function request(path, init = {}) {
  let response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init,
      headers: {
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
      cache: "no-store",
      signal: init.signal ?? AbortSignal.timeout(65_000),
    });
  } catch {
    throw new ApiError("The travel API could not be reached. Is the local backend running?", "network_error");
  }
  if (response.status === 204) return undefined;
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined"
        && window.location.pathname !== "/sign-in") {
      window.location.replace("/sign-in?expired=1");
    }
    throw new ApiError(
      payload?.error?.message ?? `The travel API returned HTTP ${response.status}.`,
      payload?.error?.code ?? "request_failed",
      payload?.error?.details ?? null,
      response.status,
    );
  }
  if (payload === null) throw new ApiError("The travel API returned an invalid response.", "invalid_response");
  return payload;
}
