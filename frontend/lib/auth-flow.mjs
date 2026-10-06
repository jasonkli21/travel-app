export const SESSION_COOKIE = "__Host-travel_session";
export const CSRF_COOKIE = "__Host-travel_csrf";
export const OAUTH_FLOW_COOKIE = "__Host-travel_oauth_flow";
export const AI_USER_TOKEN_COOKIE = "__Host-travel_ai_token";

export function sessionModeAllowed(sessionMode, deploymentMode = "local") {
  return (deploymentMode === "local" || deploymentMode === "hosted")
    && (deploymentMode === "local" || sessionMode === "google_oidc");
}

export function authCallbackCookieHeader(request) {
  const allowedNames = [SESSION_COOKIE, OAUTH_FLOW_COOKIE];
  const values = new Map();
  for (const part of (request.headers.get("cookie") ?? "").split(";")) {
    const separator = part.indexOf("=");
    if (separator < 1) continue;
    const name = part.slice(0, separator).trim();
    if (!allowedNames.includes(name)) continue;
    if (values.has(name)) values.set(name, null);
    else values.set(name, part.slice(separator + 1).trim());
  }
  const flow = values.get(OAUTH_FLOW_COOKIE);
  if (typeof flow !== "string" || !flow) return null;
  if (values.has(SESSION_COOKIE) && typeof values.get(SESSION_COOKIE) !== "string") return null;
  return allowedNames
    .filter((name) => typeof values.get(name) === "string" && values.get(name))
    .map((name) => `${name}=${values.get(name)}`)
    .join("; ");
}

const ALLOWED_RESPONSE_COOKIES = new Set([
  SESSION_COOKIE,
  CSRF_COOKIE,
  OAUTH_FLOW_COOKIE,
  AI_USER_TOKEN_COOKIE,
]);

export function validatedPublicOrigin(request, allowedHosts) {
  try {
    const authority = request.headers.get("host");
    if (!authority || authority.includes("\\")) return null;
    const url = new URL(request.url);
    const publicUrl = new URL(`${url.protocol}//${authority}`);
    if (publicUrl.username || publicUrl.password || publicUrl.pathname !== "/"
        || publicUrl.search || publicUrl.hash || !allowedHosts.includes(publicUrl.hostname)) {
      return null;
    }
    return publicUrl.origin;
  } catch {
    return null;
  }
}

export function responseAuthCookies(response) {
  let values = response.headers.getSetCookie?.() ?? [];
  if (!values.length) {
    values = (response.headers.get("set-cookie") ?? "").split(/, (?=__Host-travel_)/);
  }
  return values.filter((value) => ALLOWED_RESPONSE_COOKIES.has(value.split("=", 1)[0]));
}

export function googleAuthorizationUrl(value, { clientId, redirectUri }) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.hostname !== "accounts.google.com"
        || url.pathname !== "/o/oauth2/v2/auth" || url.username || url.password
        || !clientId || url.searchParams.getAll("client_id").length !== 1
        || url.searchParams.get("client_id") !== clientId
        || url.searchParams.get("redirect_uri") !== redirectUri
        || url.searchParams.getAll("response_type").length !== 1
        || url.searchParams.get("response_type") !== "code"
        || url.searchParams.getAll("state").length !== 1
        || !/^[A-Za-z0-9_-]{43}$/.test(url.searchParams.get("state") ?? "")
        || url.searchParams.getAll("nonce").length !== 1
        || !/^[A-Za-z0-9_-]{43}$/.test(url.searchParams.get("nonce") ?? "")
        || url.searchParams.get("code_challenge_method") !== "S256"
        || !/^[A-Za-z0-9_-]{43}$/.test(url.searchParams.get("code_challenge") ?? "")
        || url.searchParams.getAll("scope").length !== 1
        || [...(url.searchParams.get("scope") ?? "").split(/\s+/)].sort().join(" ") !== "email openid") {
      return null;
    }
    return url.toString();
  } catch {
    return null;
  }
}

export function authFailureLocation(status) {
  return status === 503 ? "/sign-in?auth=unavailable" : "/sign-in?auth=failed";
}
