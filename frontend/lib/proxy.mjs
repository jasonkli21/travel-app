import { readProxyBody } from "./proxy-response.mjs";

const MAX_REQUEST_BYTES = 64 * 1024;

function errorResponse(status, code, message) {
  return Response.json({ error: { code, message, details: null } }, { status });
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
