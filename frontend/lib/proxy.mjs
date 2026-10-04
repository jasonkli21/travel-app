import { readProxyBody } from "./proxy-response.mjs";

const MAX_REQUEST_BYTES = 64 * 1024;
const SESSION_COOKIE = "__Host-travel_session";
const CSRF_COOKIE = "__Host-travel_csrf";
const OAUTH_FLOW_COOKIE = "__Host-travel_oauth_flow";
const AI_USER_TOKEN_COOKIE = "__Host-travel_ai_token";
const RESPONSE_COOKIE_NAMES = new Set([
  SESSION_COOKIE, CSRF_COOKIE, OAUTH_FLOW_COOKIE, AI_USER_TOKEN_COOKIE,
]);

function errorResponse(status, code, message) {
  return Response.json({ error: { code, message, details: null } }, { status });
}

function cookieMap(request) {
  const cookies = new Map();
  for (const part of (request.headers.get("cookie") ?? "").split(";")) {
    const separator = part.indexOf("=");
    if (separator < 1) continue;
    const name = part.slice(0, separator).trim();
    const value = part.slice(separator + 1).trim();
    if ([SESSION_COOKIE, CSRF_COOKIE, OAUTH_FLOW_COOKIE, AI_USER_TOKEN_COOKIE].includes(name)) {
      if (cookies.has(name)) cookies.set(name, null);
      else cookies.set(name, value);
    }
  }
  return cookies;
}

function allowedCookieHeader(path, cookies) {
  const isAuth = path[0] === "auth";
  const names = isAuth
    ? (path[1] === "google" && path[2] === "callback"
      ? [OAUTH_FLOW_COOKIE]
      : path[1] === "logout" ? [SESSION_COOKIE, CSRF_COOKIE]
        : path[1] === "session" ? [SESSION_COOKIE] : [])
    : [SESSION_COOKIE, CSRF_COOKIE];
  return names
    .filter((name) => typeof cookies.get(name) === "string" && cookies.get(name))
    .map((name) => `${name}=${cookies.get(name)}`)
    .join("; ");
}

function safeSetCookies(response) {
  let values = response.headers.getSetCookie?.() ?? [];
  if (!values.length) {
    const combined = response.headers.get("set-cookie") ?? "";
    values = combined.split(/, (?=__Host-travel_)/);
  }
  return values.filter((value) => {
    const name = value.split("=", 1)[0];
    return RESPONSE_COOKIE_NAMES.has(name);
  });
}

async function readRequestBody(request) {
  const reader = request.body?.getReader();
  if (!reader) return undefined;
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_REQUEST_BYTES) {
        await reader.cancel();
        throw new RangeError("request_too_large");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

export async function proxyRequest(request, path, {
  backendBaseUrl = "http://localhost:8000",
  allowedHosts = ["localhost", "127.0.0.1", "[::1]"],
  fetchImpl = fetch,
} = {}) {
  const url = new URL(request.url);
  const origin = request.headers.get("origin");
  // Next's request URL can use its internal listening hostname. Validate the
  // browser's Host authority, then compare Origin against that exact authority.
  let publicUrl;
  try {
    const authority = request.headers.get("host") ?? url.host;
    publicUrl = new URL(`${url.protocol}//${authority}`);
    if (publicUrl.username || publicUrl.password || publicUrl.pathname !== "/"
        || publicUrl.search || publicUrl.hash || authority.includes("\\")) {
      throw new Error("invalid_host");
    }
  } catch {
    return errorResponse(400, "invalid_host", "This web host is not allowed.");
  }
  if (!allowedHosts.includes(publicUrl.hostname)) {
    return errorResponse(400, "invalid_host", "This web host is not allowed.");
  }
  if ((origin !== null && origin !== publicUrl.origin)
      || request.headers.get("sec-fetch-site") === "cross-site") {
    return errorResponse(403, "invalid_origin", "This browser origin is not allowed.");
  }
  if (!path.length || path.some((part) => !/^[a-zA-Z0-9_-]+$/.test(part))) {
    return errorResponse(400, "invalid_path", "This API path is not valid.");
  }
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const expectedRevision = request.headers.get("x-expected-revision");
  if (expectedRevision !== null) headers.set("x-expected-revision", expectedRevision);
  const cookies = cookieMap(request);
  const cookieHeader = allowedCookieHeader(path, cookies);
  if (cookieHeader) headers.set("cookie", cookieHeader);
  const aiCall = path.some((part) => part === "research" || part === "proposals");
  const aiUserToken = cookies.get(AI_USER_TOKEN_COOKIE);
  if (aiCall && typeof aiUserToken === "string" && aiUserToken) {
    headers.set("x-user-id-token", aiUserToken);
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(request.method.toUpperCase())) {
    headers.set("origin", publicUrl.origin);
    const csrf = request.headers.get("x-csrf-token");
    if (csrf) headers.set("x-csrf-token", csrf);
  }
  try {
    const response = await fetchImpl(
      `${backendBaseUrl.trim().replace(/\/+$/, "")}/v1/${path.join("/")}${url.search}`,
      {
        method: request.method,
        headers,
        body: request.method === "GET" || request.method === "HEAD"
          ? undefined : await readRequestBody(request),
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(60_000),
      },
    );
    const responseHeaders = new Headers({ "Cache-Control": "no-store" });
    for (const name of ["content-type", "x-request-id"]) {
      const value = response.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    for (const cookie of safeSetCookies(response)) responseHeaders.append("set-cookie", cookie);
    return new Response(await readProxyBody(response), {
      status: response.status,
      headers: responseHeaders,
    });
  } catch (error) {
    if (error instanceof RangeError) {
      return errorResponse(413, "request_too_large", "The request body exceeds 64 KiB.");
    }
    return errorResponse(503, "travel_api_unavailable", "The travel API is unavailable. Try again.");
  }
}
