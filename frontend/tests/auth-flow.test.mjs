import assert from "node:assert/strict";
import test from "node:test";

import {
  AI_USER_TOKEN_COOKIE,
  CSRF_COOKIE,
  OAUTH_FLOW_COOKIE,
  SESSION_COOKIE,
  authCallbackCookieHeader,
  authFailureLocation,
  googleAuthorizationUrl,
  responseAuthCookies,
  sessionModeAllowed,
  validatedPublicOrigin,
} from "../lib/auth-flow.mjs";

test("hosted page sessions fail closed unless API identity is Google verified", () => {
  assert.equal(sessionModeAllowed("local", "local"), true);
  assert.equal(sessionModeAllowed("google_oidc", "hosted"), true);
  assert.equal(sessionModeAllowed("local", "hosted"), false);
  assert.equal(sessionModeAllowed("local", "invalid"), false);
});

test("auth routes accept only configured browser hosts and preserve that origin", () => {
  const request = new Request("https://next-internal/auth/google/callback", {
    headers: { host: "travel.example.test:443" },
  });
  assert.equal(validatedPublicOrigin(request, ["travel.example.test"]), "https://travel.example.test");
  for (const host of ["evil.example.test", "travel.example.test@evil.test", "travel.example.test/path"]) {
    assert.equal(validatedPublicOrigin(new Request("https://internal/", { headers: { host } }), ["travel.example.test"]), null);
  }
});

test("OAuth redirects are restricted to Google's authorization-code endpoint", () => {
  const options = { clientId: "client", redirectUri: "https://travel.example.test/auth/google/callback" };
  const random = "1234567890123456789012345678901234567890123";
  const allowed = `https://accounts.google.com/o/oauth2/v2/auth?client_id=client&redirect_uri=https%3A%2F%2Ftravel.example.test%2Fauth%2Fgoogle%2Fcallback&response_type=code&scope=openid+email&state=${random}&nonce=${random}&code_challenge=${random}&code_challenge_method=S256`;
  assert.equal(googleAuthorizationUrl(allowed, options), allowed);
  assert.equal(googleAuthorizationUrl("https://attacker.test/o/oauth2/v2/auth?client_id=x", options), null);
  assert.equal(googleAuthorizationUrl("https://accounts.google.com.evil.test/o/oauth2/v2/auth?client_id=x", options), null);
  assert.equal(googleAuthorizationUrl("javascript:alert(1)", options), null);
  assert.equal(googleAuthorizationUrl(allowed.replace("travel.example.test", "attacker.test"), options), null);
  assert.equal(authFailureLocation(503), "/sign-in?auth=unavailable");
  assert.equal(authFailureLocation(401), "/sign-in?auth=failed");
});

test("the Next callback copies only the four host-prefixed auth cookies", () => {
  const headers = new Headers();
  for (const cookie of [
    `${SESSION_COOKIE}=opaque; Secure; HttpOnly; Path=/; SameSite=Lax`,
    `${CSRF_COOKIE}=csrf; Secure; Path=/; SameSite=Strict`,
    `${OAUTH_FLOW_COOKIE}=flow; Secure; HttpOnly; Path=/; SameSite=Lax`,
    `${AI_USER_TOKEN_COOKIE}=private; Secure; HttpOnly; Path=/; SameSite=Lax`,
    "other=private; Path=/",
  ]) headers.append("Set-Cookie", cookie);
  const response = new Response("ok", { headers });
  const cookies = responseAuthCookies(response);
  assert.equal(cookies.length, 4);
  assert.ok(cookies.some((value) => value.startsWith(`${SESSION_COOKIE}=`)));
  assert.ok(cookies.some((value) => value.startsWith(`${CSRF_COOKIE}=`)));
  assert.ok(cookies.some((value) => value.startsWith(`${OAUTH_FLOW_COOKIE}=`)));
  assert.ok(cookies.some((value) => value.startsWith(`${AI_USER_TOKEN_COOKIE}=`)));
  assert.ok(cookies.every((value) => !value.startsWith("other=")));
});

test("the OAuth callback forwards only its flow cookie and existing opaque session", () => {
  const request = new Request("https://travel.example.test/auth/google/callback", {
    headers: {
      cookie: `${SESSION_COOKIE}=existing-session; ${OAUTH_FLOW_COOKIE}=one-time-flow; other=private`,
    },
  });
  assert.equal(
    authCallbackCookieHeader(request),
    `${SESSION_COOKIE}=existing-session; ${OAUTH_FLOW_COOKIE}=one-time-flow`,
  );
  assert.equal(authCallbackCookieHeader(new Request(request.url)), null);
  assert.equal(authCallbackCookieHeader(new Request(request.url, {
    headers: { cookie: `${SESSION_COOKIE}=one; ${SESSION_COOKIE}=two; ${OAUTH_FLOW_COOKIE}=flow` },
  })), null);
});
