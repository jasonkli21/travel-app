import { NextRequest } from "next/server";

import {
  authFailureLocation,
  googleAuthorizationUrl,
  responseAuthCookies,
  validatedPublicOrigin,
} from "../../../../lib/auth-flow.mjs";

export const dynamic = "force-dynamic";

function safeRedirect(location: string, status: number): Response {
  return new Response(null, {
    status,
    headers: { "Location": location, "Cache-Control": "no-store" },
  });
}

export async function GET(request: NextRequest): Promise<Response> {
  const allowedHosts = (process.env.TRAVEL_WEB_ALLOWED_HOSTS ?? "localhost,127.0.0.1,[::1]")
    .split(",").map((host) => host.trim().toLowerCase());
  const publicOrigin = validatedPublicOrigin(request, allowedHosts);
  const origin = request.headers.get("origin");
  if (!publicOrigin) {
    return Response.json({ error: "This web host is not allowed." }, {
      status: 400,
      headers: { "Cache-Control": "no-store" },
    });
  }
  if ((origin !== null && origin !== publicOrigin)
      || request.headers.get("sec-fetch-site") === "cross-site") {
    return Response.json({ error: "This browser origin is not allowed." }, {
      status: 403,
      headers: { "Cache-Control": "no-store" },
    });
  }
  try {
    const baseUrl = process.env.TRAVEL_API_URL ?? "http://localhost:8000";
    const upstream = await fetch(`${baseUrl.replace(/\/+$/, "")}/v1/auth/google/start`, {
      method: "GET",
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(7_000),
    });
    if (!upstream.ok) return safeRedirect(authFailureLocation(upstream.status), 303);
    const payload: unknown = await upstream.json();
    const rawAuthorizationUrl = payload && typeof payload === "object"
      ? (payload as Record<string, unknown>).authorization_url
      : null;
    const authorizationUrl = typeof rawAuthorizationUrl === "string"
      ? googleAuthorizationUrl(rawAuthorizationUrl, {
        clientId: process.env.GOOGLE_OAUTH_CLIENT_ID ?? "",
        redirectUri: process.env.GOOGLE_OAUTH_REDIRECT_URI ?? "",
      })
      : null;
    if (!authorizationUrl) return safeRedirect(authFailureLocation(502), 303);
    const response = safeRedirect(authorizationUrl, 302);
    for (const cookie of responseAuthCookies(upstream)) response.headers.append("Set-Cookie", cookie);
    return response;
  } catch {
    return safeRedirect(authFailureLocation(503), 303);
  }
}
