import "server-only";

import { cookies } from "next/headers";

import { SESSION_COOKIE } from "./auth-flow.mjs";

export type PageSession = {
  mode: "local" | "google_oidc";
  authenticated: boolean;
  email: string | null;
  expires_at: string | null;
};

export type SessionLookup =
  | { kind: "ok"; session: PageSession }
  | { kind: "unauthenticated"; mode: PageSession["mode"] }
  | { kind: "unavailable" };

export async function lookupPageSession(): Promise<SessionLookup> {
  const sessionCookie = (await cookies()).get(SESSION_COOKIE)?.value;
  const headers = new Headers();
  if (sessionCookie) headers.set("cookie", `${SESSION_COOKIE}=${sessionCookie}`);
  try {
    const baseUrl = process.env.TRAVEL_API_URL ?? "http://localhost:8000";
    const response = await fetch(`${baseUrl.replace(/\/+$/, "")}/v1/auth/session`, {
      headers,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(5_000),
    });
    if (response.status === 401) return { kind: "unavailable" };
    if (!response.ok) return { kind: "unavailable" };
    const payload: unknown = await response.json();
    if (!payload || typeof payload !== "object") return { kind: "unavailable" };
    const value = payload as Record<string, unknown>;
    if ((value.mode !== "local" && value.mode !== "google_oidc")
        || typeof value.authenticated !== "boolean"
        || (value.email !== null && typeof value.email !== "string")
        || (value.expires_at !== null && typeof value.expires_at !== "string")) {
      return { kind: "unavailable" };
    }
    const session: PageSession = {
      mode: value.mode,
      authenticated: value.authenticated,
      email: value.email,
      expires_at: value.expires_at,
    };
    return session.authenticated
      ? { kind: "ok", session }
      : { kind: "unauthenticated", mode: session.mode };
  } catch {
    return { kind: "unavailable" };
  }
}
