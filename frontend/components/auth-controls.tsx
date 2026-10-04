"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

type SessionStatus = { mode: string; authenticated: boolean; email?: string | null };

function readCookie(name: string): string | null {
  const matches = document.cookie.split(";")
    .map((part) => part.trim())
    .filter((part) => part.startsWith(`${name}=`));
  return matches.length === 1 ? matches[0].slice(name.length + 1) : null;
}

export default function AuthControls() {
  const router = useRouter();
  const [session, setSession] = useState<SessionStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let mounted = true;
    void fetch("/api/v1/auth/session", { cache: "no-store", credentials: "same-origin" })
      .then(async (response) => {
        const payload = await response.json().catch(() => null);
        if (!response.ok || !payload || typeof payload.authenticated !== "boolean") {
          throw new Error("session_unavailable");
        }
        if (mounted) setSession(payload as SessionStatus);
      })
      .catch(() => {
        if (mounted) setError("Session status is unavailable.");
      });
    return () => { mounted = false; };
  }, []);

  const logout = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    const csrf = readCookie("__Host-travel_csrf");
    if (!csrf) {
      setError("Session verification expired. Sign in again.");
      setBusy(false);
      router.replace("/sign-in?expired=1");
      return;
    }
    try {
      const response = await fetch("/api/v1/auth/logout", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "X-CSRF-Token": csrf },
      });
      if (response.status === 204 || response.status === 401) {
        router.replace("/sign-in?expired=1");
        return;
      }
      setError("Sign out could not be completed. Try again.");
    } catch {
      setError("Sign out could not be completed. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <header className="authToolbar">
      <Link href="/" className="authBrand">Personal Travel</Link>
      <div className="authStatus" aria-live="polite">
        {session?.mode === "local" ? <span>Local mode</span> : null}
        {session?.authenticated && session.mode === "google_oidc"
          ? <><span>{session.email ?? "Signed in"}</span><button type="button" className="secondary compact" disabled={busy} onClick={() => void logout()}>{busy ? "Signing out…" : "Sign out"}</button></>
          : session && !session.authenticated ? <Link href="/sign-in">Sign in</Link> : null}
        {!session && !error ? <span className="muted">Checking session…</span> : null}
        {error ? <span role="status" className="muted">{error} <Link href="/sign-in">Sign in</Link></span> : null}
      </div>
    </header>
  );
}
