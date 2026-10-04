export class ApiError extends Error {
  constructor(message, code = "request_failed", details = null, status = null) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.details = details;
    this.status = status;
  }
}

// The CSRF cookie is readable by the browser; the session cookie is not.
// Reject ambiguous cookie headers instead of choosing an attacker-controlled copy.
export function csrfCookieValue(cookieHeader) {
  const values = cookieHeader.split(";").map((part) => part.trim())
    .filter((part) => part.startsWith("__Host-travel_csrf="));
  if (values.length !== 1) return null;
  const value = values[0].slice("__Host-travel_csrf=".length);
  return /^[A-Za-z0-9_-]{43}$/.test(value) ? value : null;
}

export async function request(path, init = {}) {
  let response;
  try {
    const method = (init.method ?? "GET").toUpperCase();
    const headers = new Headers(init.headers);
    if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    if (!["GET", "HEAD", "OPTIONS"].includes(method) && typeof document !== "undefined") {
      const csrf = csrfCookieValue(document.cookie);
      if (csrf) headers.set("X-CSRF-Token", csrf);
      else headers.delete("X-CSRF-Token");
    }
    response = await fetch(`/api/v1${path}`, {
      ...init,
      headers,
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
