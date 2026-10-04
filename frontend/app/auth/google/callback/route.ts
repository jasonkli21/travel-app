import { NextRequest } from "next/server";

import {
  authCallbackCookieHeader,
  authFailureLocation,
  responseAuthCookies,
  validatedPublicOrigin,
} from "../../../../lib/auth-flow.mjs";

export const dynamic = "force-dynamic";

function safeRedirect(location: string, cookies: string[] = []): Response {
  const headers = new Headers({ "Location": location, "Cache-Control": "no-store" });
  for (const cookie of cookies) headers.append("Set-Cookie", cookie);
  return new Response(null, { status: 303, headers });
}

export async function GET(request: NextRequest): Promise<Response> {
  const allowedHosts = (process.env.TRAVEL_WEB_ALLOWED_HOSTS ?? "localhost,127.0.0.1,[::1]")
    .split(",").map((host) => host.trim().toLowerCase());
  if (!validatedPublicOrigin(request, allowedHosts)) return safeRedirect("/sign-in?auth=failed");

  const { searchParams } = new URL(request.url);
  const codes = searchParams.getAll("code");
  const states = searchParams.getAll("state");
  const code = codes.length === 1 ? codes[0] : "";
  const state = states.length === 1 ? states[0] : "";
  if (searchParams.has("error") || code.length < 1 || code.length > 2048
      || /[\s\u0000-\u001f\u007f]/.test(code)
      || !/^[A-Za-z0-9_-]{43}$/.test(state)) {
    return safeRedirect("/sign-in?auth=failed");
  }
  const cookie = authCallbackCookieHeader(request);
  if (!cookie) return safeRedirect("/sign-in?auth=failed");

  try {
    const baseUrl = process.env.TRAVEL_API_URL ?? "http://localhost:8000";
    const upstream = await fetch(`${baseUrl.replace(/\/+$/, "")}/v1/auth/google/callback`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Cookie: cookie },
      body: JSON.stringify({ code, state }),
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(8_000),
    });
    if (!upstream.ok) return safeRedirect(authFailureLocation(upstream.status));
    return safeRedirect("/", responseAuthCookies(upstream));
  } catch {
    return safeRedirect(authFailureLocation(503));
  }
}
