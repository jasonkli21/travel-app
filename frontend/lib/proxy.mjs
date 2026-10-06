import { readProxyBody } from "./proxy-response.mjs";

const MAX_REQUEST_BYTES = 64 * 1024;
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const MAX_PRIVATE_DOWNLOAD_BYTES = 10 * 1024 * 1024;
const MAX_EXPORT_RESPONSE_BYTES = 36 * 1024 * 1024;
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

function isImportUpload(path, method) {
  return method === "POST" && path.length === 3 && path[0] === "trips"
    && /^[0-9a-fA-F-]{36}$/.test(path[1]) && path[2] === "imports";
}

function isAttachmentUpload(path, method) {
  return method === "POST" && path.length === 3 && path[0] === "trips"
    && /^[0-9a-fA-F-]{36}$/.test(path[1]) && path[2] === "attachments";
}

function isTripExport(path, method) {
  return method === "POST" && path.length === 3 && path[0] === "trips"
    && /^[0-9a-fA-F-]{36}$/.test(path[1]) && path[2] === "exports";
}

function isAttachmentDownload(path, method) {
  return method === "GET" && path.length === 5 && path[0] === "trips"
    && /^[0-9a-fA-F-]{36}$/.test(path[1]) && path[2] === "attachments"
    && /^[0-9a-fA-F-]{36}$/.test(path[3]) && path[4] === "download";
}

function streamedUpload(request, maxBytes, abortController, onTooLarge) {
  const reader = request.body?.getReader();
  if (!reader) return undefined;
  let total = 0;
  let cancelled = false;
  const cancelReader = async (reason) => {
    cancelled = true;
    try { await reader.cancel(reason); } catch { /* the client stream may already be closed */ }
    try { reader.releaseLock(); } catch { /* release is best effort after a pending read */ }
  };
  const onAbort = () => { void cancelReader(abortController.signal.reason); };
  abortController.signal.addEventListener("abort", onAbort, { once: true });
  return new ReadableStream({
    async pull(controller) {
      try {
        const { value, done } = await reader.read();
        if (done) {
          if (!cancelled) controller.close();
          abortController.signal.removeEventListener("abort", onAbort);
          try { reader.releaseLock(); } catch { /* already released on cancellation */ }
          return;
        }
        total += value.byteLength;
        if (total > maxBytes) {
          onTooLarge();
          abortController.abort();
          await cancelReader("upload exceeds byte limit");
          controller.error(new RangeError("upload_too_large"));
          return;
        }
        if (!cancelled) controller.enqueue(value);
      } catch (error) {
        if (!cancelled) controller.error(error);
      }
    },
    async cancel(reason) {
      abortController.signal.removeEventListener("abort", onAbort);
      await cancelReader(reason);
    },
  });
}

function streamedBoundedResponse(response, abortController, timeout, maxBytes, onFinish) {
  const reader = response.body?.getReader();
  if (!reader) {
    onFinish();
    return null;
  }
  let total = 0;
  let finished = false;
  let abortReason = null;
  const finish = () => {
    if (finished) return;
    finished = true;
    clearTimeout(timeout);
    abortController.signal.removeEventListener("abort", onAbort);
    try { reader.releaseLock(); } catch { /* best effort after cancellation */ }
    onFinish();
  };
  const cancelReader = async (reason) => {
    try { await reader.cancel(reason); } catch { /* already closed or aborted */ }
    finish();
  };
  const onAbort = () => {
    abortReason = abortController.signal.reason ?? new DOMException("Source stream aborted.", "AbortError");
    void cancelReader(abortReason);
  };
  abortController.signal.addEventListener("abort", onAbort, { once: true });
  return new ReadableStream({
    async pull(streamController) {
      try {
        const { value, done } = await reader.read();
        if (abortController.signal.aborted) {
          finish();
          streamController.error(abortReason ?? new DOMException("Source stream aborted.", "AbortError"));
          return;
        }
        if (done) {
          finish();
          streamController.close();
          return;
        }
        total += value.byteLength;
        if (total > maxBytes) {
          abortController.abort("source response exceeds byte limit");
          await cancelReader("source response exceeds byte limit");
          streamController.error(new RangeError("binary_response_too_large"));
          return;
        }
        streamController.enqueue(value);
      } catch (error) {
        finish();
        streamController.error(error);
      }
    },
    async cancel(reason) {
      abortController.abort(reason);
      await cancelReader(reason);
    },
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
  sourceResponseTimeoutMs = 30_000,
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
  const method = request.method.toUpperCase();
  const importUpload = isImportUpload(path, method);
  const attachmentUpload = isAttachmentUpload(path, method);
  const upload = importUpload || attachmentUpload;
  const tripExport = isTripExport(path, method);
  const isImportSource = method === "GET" && path.length === 5 && path[0] === "trips"
    && /^[0-9a-fA-F-]{36}$/.test(path[1]) && path[2] === "imports"
    && /^[0-9a-fA-F-]{36}$/.test(path[3]) && path[4] === "source";
  const attachmentDownload = isAttachmentDownload(path, method);
  const isPrivateDownload = isImportSource || attachmentDownload;
  const isBinaryRequest = isPrivateDownload || tripExport;
  const binaryLimit = tripExport ? MAX_EXPORT_RESPONSE_BYTES : MAX_PRIVATE_DOWNLOAD_BYTES;
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  if (upload) {
    const mediaType = (contentType ?? "").split(";", 1)[0].trim().toLowerCase();
    const allowedMedia = importUpload
      ? ["text/plain", "application/pdf"]
      : ["text/plain", "application/pdf", "image/jpeg", "image/png"];
    if (!allowedMedia.includes(mediaType)) {
      return errorResponse(415, "unsupported_media_type", importUpload
        ? "Use plain text or PDF."
        : "Use plain text, PDF, JPEG, or PNG.");
    }
    if (importUpload) {
      const requestKey = request.headers.get("x-import-request-key");
      if (!requestKey || !/^[A-Za-z0-9_-]{8,128}$/.test(requestKey)) {
        return errorResponse(400, "invalid_request_key", "A valid import request key is required.");
      }
      headers.set("x-import-request-key", requestKey);
      const retention = request.headers.get("x-source-retention") ?? "delete_after_confirmation";
      if (!new Set(["delete_after_confirmation", "keep_until_expiry"]).has(retention)) {
        return errorResponse(400, "invalid_retention_choice", "Choose a supported source retention option.");
      }
      headers.set("x-source-retention", retention);
      const filename = request.headers.get("x-source-filename");
      if (filename) headers.set("x-source-filename", filename.slice(0, 512));
    } else {
      const requestKey = request.headers.get("x-attachment-request-key");
      if (!requestKey || !/^[A-Za-z0-9_-]{8,128}$/.test(requestKey)) {
        return errorResponse(400, "invalid_request_key", "A valid attachment request key is required.");
      }
      headers.set("x-attachment-request-key", requestKey);
      const filename = request.headers.get("x-attachment-filename");
      if (filename) headers.set("x-attachment-filename", filename.slice(0, 512));
      const reservationId = request.headers.get("x-attachment-reservation-id");
      if (reservationId) {
        if (!/^[0-9a-fA-F-]{36}$/.test(reservationId)) {
          return errorResponse(400, "invalid_reservation_id", "A valid reservation link is required.");
        }
        headers.set("x-attachment-reservation-id", reservationId);
      }
    }
    const declared = request.headers.get("content-length");
    const cap = mediaType === "text/plain" ? 1024 * 1024 : MAX_UPLOAD_BYTES;
    if (declared && (!/^\d+$/.test(declared) || Number(declared) > cap)) {
      return errorResponse(413, "request_too_large", "The upload exceeds its size limit.");
    }
  }
  const expectedRevision = request.headers.get("x-expected-revision");
  if (expectedRevision !== null) headers.set("x-expected-revision", expectedRevision);
  const cookies = cookieMap(request);
  const cookieHeader = allowedCookieHeader(path, cookies);
  if (cookieHeader) headers.set("cookie", cookieHeader);
  const isBookingExtraction = path.length === 5 && path[0] === "trips"
    && /^[0-9a-fA-F-]{36}$/.test(path[1]) && path[2] === "imports"
    && /^[0-9a-fA-F-]{36}$/.test(path[3]) && path[4] === "extract";
  const isImportCleanup = path.length === 5 && path[0] === "trips"
    && /^[0-9a-fA-F-]{36}$/.test(path[1]) && path[2] === "imports"
    && /^[0-9a-fA-F-]{36}$/.test(path[3])
    && ((path[4] === "confirm" || path[4] === "reject") && request.method === "POST"
      || path[4] === "source" && request.method === "DELETE");
  const isDeletionRetry = path.length === 2 && path[0] === "private-import-deletion-intents"
    && path[1] === "retry" && request.method === "POST";
  const aiCall = path.some((part) => part === "research" || part === "proposals")
    || isBookingExtraction || isImportCleanup || isDeletionRetry;
  const aiUserToken = cookies.get(AI_USER_TOKEN_COOKIE);
  if (aiCall && typeof aiUserToken === "string" && aiUserToken) {
    headers.set("x-user-id-token", aiUserToken);
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(request.method.toUpperCase())) {
    headers.set("origin", publicUrl.origin);
    const csrf = request.headers.get("x-csrf-token");
    if (csrf) headers.set("x-csrf-token", csrf);
  }
  const controller = new AbortController();
  let uploadTooLarge = false;
  let uploadTimedOut = false;
  let sourceTimedOut = false;
  let sourceStreamOwnsTimeout = false;
  const timeoutMs = upload ? 30_000 : isPrivateDownload ? sourceResponseTimeoutMs : 60_000;
  const timeout = setTimeout(() => {
    if (upload) uploadTimedOut = true;
    if (isPrivateDownload) sourceTimedOut = true;
    if (tripExport) sourceTimedOut = true;
    controller.abort();
  }, timeoutMs);
  try {
    const response = await fetchImpl(
      `${backendBaseUrl.trim().replace(/\/+$/, "")}/v1/${path.join("/")}${url.search}`,
      {
        method: request.method,
        headers,
        body: request.method === "GET" || request.method === "HEAD"
          ? undefined : upload
            ? streamedUpload(
              request,
              contentType.split(";", 1)[0].trim().toLowerCase() === "text/plain"
                ? 1024 * 1024 : MAX_UPLOAD_BYTES,
              controller,
              () => { uploadTooLarge = true; },
            )
            : await readRequestBody(request),
        ...(upload ? { duplex: "half" } : {}),
        cache: "no-store",
        redirect: "error",
        signal: controller.signal,
      },
    );
    const responseHeaders = new Headers({ "Cache-Control": "no-store" });
    for (const name of ["content-type", "x-request-id"]) {
      const value = response.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    for (const cookie of safeSetCookies(response)) responseHeaders.append("set-cookie", cookie);
    if (isBinaryRequest && response.ok) {
      for (const name of ["content-disposition", "x-content-type-options"]) {
        const value = response.headers.get(name);
        if (value) responseHeaders.set(name, value);
      }
      for (const name of ["x-trip-revision", "x-trip-generated-at"]) {
        const value = response.headers.get(name);
        if (value) responseHeaders.set(name, value);
      }
      const declaredSize = response.headers.get("content-length");
      if (declaredSize && /^\d+$/.test(declaredSize)
          && Number(declaredSize) > binaryLimit) {
        controller.abort("binary response exceeds byte limit");
        try { await response.body?.cancel("binary response exceeds byte limit"); } catch { /* closed */ }
        return errorResponse(502, "binary_response_too_large", "The download exceeds its size limit.");
      }
      const body = streamedBoundedResponse(response, controller, timeout, binaryLimit, () => {
        sourceStreamOwnsTimeout = false;
      });
      sourceStreamOwnsTimeout = body !== null;
      return new Response(body, {
        status: response.status,
        headers: responseHeaders,
      });
    }
    return new Response(await readProxyBody(response), {
      status: response.status,
      headers: responseHeaders,
    });
  } catch (error) {
    if (uploadTooLarge) {
      return errorResponse(413, "request_too_large", "The upload exceeds its size limit.");
    }
    if (uploadTimedOut) {
      return errorResponse(408, "upload_timeout", "The upload took too long.");
    }
    if (sourceTimedOut) {
      return errorResponse(408, "download_timeout", "The download took too long.");
    }
    if (error instanceof RangeError) {
      return errorResponse(413, "request_too_large", "The request body exceeds 64 KiB.");
    }
    return errorResponse(503, "travel_api_unavailable", "The travel API is unavailable. Try again.");
  } finally {
    if (!sourceStreamOwnsTimeout) clearTimeout(timeout);
  }
}
