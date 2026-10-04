import { redirect } from "next/navigation";

import { lookupPageSession } from "../../lib/server-auth";

export const dynamic = "force-dynamic";

type SignInPageProps = {
  searchParams: Promise<{ auth?: string; expired?: string }>;
};

function statusMessage(auth: string | undefined, expired: string | undefined): string | null {
  if (expired === "1") return "Your session expired or was signed out. Sign in again to continue.";
  if (auth === "unavailable") return "Sign-in is temporarily unavailable. Try again in a moment.";
  if (auth === "failed") return "Google sign-in could not be completed. Start again to retry.";
  return null;
}

export default async function SignInPage({ searchParams }: SignInPageProps) {
  const [status, params] = await Promise.all([lookupPageSession(), searchParams]);
  if (status.kind === "ok") redirect("/");
  const message = statusMessage(params.auth, params.expired);
  return (
    <main className="signInShell">
      <section className="signInPanel" aria-labelledby="sign-in-title">
        <p className="eyebrow">PERSONAL TRAVEL</p>
        <h1 id="sign-in-title">Sign in to your travel workspace</h1>
        <p className="muted">Your trips are private to your verified account. Travel data stays in this application.</p>
        {status.kind === "unavailable" && !message
          ? <p className="errorBanner" role="status">The travel service is unavailable. Try again when it is reachable.</p>
          : null}
        {message ? <p className="errorBanner" role="status">{message}</p> : null}
        {status.kind === "unavailable"
          ? <a className="secondary signInButton" href="/sign-in">Try again</a>
          : <a className="primary signInButton" href="/auth/google/start">Continue with Google</a>}
      </section>
    </main>
  );
}
