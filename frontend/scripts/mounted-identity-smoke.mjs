// Run only against the disposable synthetic identity fixture and mounted web build.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { travelApi } from "../lib/api.ts";
import { bookingImportApi } from "../lib/booking-imports.ts";

if (process.env.SYNTHETIC_IDENTITY_FIXTURE !== "1") {
  throw new Error("Synthetic identity fixture must be explicitly enabled.");
}

const base = process.env.MOUNTED_WEB_URL ?? "http://localhost:3000";
const realFetch = globalThis.fetch;
const cookies = new Map();

function ingest(response) {
  for (const header of response.headers.getSetCookie()) {
    const [name, value] = header.split(";", 1)[0].split("=", 2);
    if (name.startsWith("__Host-travel_")) {
      if (value && value !== '""') cookies.set(name, value);
      else cookies.delete(name);
    }
  }
}

function cookieHeader() {
  return [...cookies].map(([name, value]) => `${name}=${value}`).join("; ");
}

async function mounted(path, init = {}) {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (cookies.size) headers.set("Cookie", cookieHeader());
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers.set("Origin", base);
  const response = await realFetch(new URL(path, base), {
    ...init, headers, redirect: "manual",
  });
  ingest(response);
  return response;
}

const privateBefore = await mounted("/");
assert.equal(privateBefore.status, 307);
assert.equal(privateBefore.headers.get("location"), "/sign-in");
const start = await mounted("/auth/google/start");
assert.equal(start.status, 302);
assert.ok(cookies.has("__Host-travel_oauth_flow"));
const googleUrl = new URL(start.headers.get("location"));
assert.equal(googleUrl.hostname, "accounts.google.com");
const state = googleUrl.searchParams.get("state");
assert.ok(state);

const callback = await mounted(`/auth/google/callback?code=synthetic-code&state=${state}`);
assert.equal(callback.status, 303);
assert.equal(callback.headers.get("location"), "/");
assert.ok(cookies.has("__Host-travel_session"));
assert.ok(cookies.has("__Host-travel_csrf"));
assert.equal(cookies.has("__Host-travel_ai_token"), false);
const session = await mounted("/api/v1/auth/session");
assert.equal((await session.json()).authenticated, true);

// Use the actual typed client against the mounted Next proxy and FastAPI.
globalThis.document = { cookie: `__Host-travel_csrf=${cookies.get("__Host-travel_csrf")}` };
globalThis.fetch = (url, init) => mounted(url, init);
const input = {
  title: "Synthetic identity smoke",
  start_date: "2026-01-01",
  end_date: "2026-01-01",
  timezone: "UTC",
};
const trip = await travelApi.createTrip(input);
assert.ok(trip.id);
assert.ok((await travelApi.listTrips()).some((row) => row.id === trip.id));
const privateAfter = await mounted(`/trips/${trip.id}`);
assert.equal(privateAfter.status, 200);
const workspaceHtml = await privateAfter.text();

if (process.env.SYNTHETIC_BOOKING_IMPORT_FIXTURE === "1") {
  assert.match(workspaceHtml, /Loading trip workspace/);
  const bookingText = "Booking confirmation: Synthetic Hotel reservation for 2026-10-05 at 09:30 +09:00";
  const uploaded = await bookingImportApi.upload(
    trip.id,
    bookingText,
    "text/plain",
    "synthetic-booking-import-01",
    "delete_after_confirmation",
  );
  assert.equal(uploaded.state, "received");
  const review = await bookingImportApi.extract(trip.id, uploaded.id);
  assert.equal(review.state, "review_ready", JSON.stringify(review));
  assert.equal(review.source_state, "ready");
  assert.equal(review.candidates.length, 1);
  assert.match(review.candidates[0].source_excerpt, /Booking confirmation/);

  const candidate = review.candidates[0];
  const confirmation = {
    confirmation_key: crypto.randomUUID(),
    expected_trip_revision: trip.revision,
    expected_import_revision: review.review_revision,
    entries: [{
      candidate_id: candidate.candidate_id,
      decision: "create_separate",
      provider_name: candidate.current.provider_name,
      reservation_type: candidate.current.reservation_type,
      reservation_status: "tentative",
      starts_at_date: candidate.current.starts_at_date,
      starts_at_time: candidate.current.starts_at_time,
      starts_at_timezone: candidate.current.starts_at_timezone,
    }],
  };
  const saved = await bookingImportApi.confirm(trip.id, uploaded.id, confirmation);
  assert.equal(saved.outcomes[0].outcome, "created");
  assert.ok(saved.outcomes[0].reservation_id);
  assert.deepEqual(
    await bookingImportApi.confirm(trip.id, uploaded.id, confirmation),
    saved,
  );
  const recovered = await bookingImportApi.get(trip.id, uploaded.id);
  assert.equal(recovered.state, "applied");
  assert.equal(recovered.source_state, "deleted");
  assert.equal(recovered.confirmation_outcome?.confirmation_key, confirmation.confirmation_key);
  assert.equal(recovered.upstream_delete_pending, false);
  assert.equal((await travelApi.listReservations(trip.id)).length, 1);

  const pdfPath = process.env.SYNTHETIC_BOOKING_PDF_PATH;
  if (!pdfPath) throw new Error("A generated synthetic PDF path is required for the booking smoke.");
  const pdfBytes = await readFile(pdfPath);
  const pdfImport = await bookingImportApi.upload(
    trip.id,
    new Blob([pdfBytes], { type: "application/pdf" }),
    "application/pdf",
    "synthetic-booking-pdf-import-01",
    "delete_after_confirmation",
    "synthetic-booking.pdf",
  );
  assert.equal(pdfImport.media_type, "application/pdf");
  const pdfReview = await bookingImportApi.extract(trip.id, pdfImport.id);
  assert.equal(pdfReview.state, "review_ready");
  assert.equal(pdfReview.source_state, "ready");
  assert.equal(pdfReview.candidates.length, 1);
  assert.match(pdfReview.candidates[0].source_excerpt, /2026-10-05 at 09:30 \+09:00/);
  const pdfCandidate = pdfReview.candidates[0];
  const tripAfterTextImport = await travelApi.getTrip(trip.id);
  const pdfConfirmation = {
    confirmation_key: crypto.randomUUID(),
    expected_trip_revision: tripAfterTextImport.revision,
    expected_import_revision: pdfReview.review_revision,
    entries: [{
      candidate_id: pdfCandidate.candidate_id,
      decision: "create_separate",
      provider_name: pdfCandidate.current.provider_name,
      reservation_type: pdfCandidate.current.reservation_type,
      reservation_status: "confirmed",
      starts_at_date: pdfCandidate.current.starts_at_date,
      starts_at_time: pdfCandidate.current.starts_at_time,
      starts_at_timezone: pdfCandidate.current.starts_at_timezone,
    }],
  };
  const pdfSaved = await bookingImportApi.confirm(trip.id, pdfImport.id, pdfConfirmation);
  assert.equal(pdfSaved.outcomes[0].outcome, "created");
  assert.equal((await travelApi.listReservations(trip.id)).length, 2);
  const pdfRecovered = await bookingImportApi.get(trip.id, pdfImport.id);
  assert.equal(pdfRecovered.state, "applied");
  assert.equal(pdfRecovered.source_state, "deleted");
  assert.equal(pdfRecovered.upstream_delete_pending, false);
}

const missingCsrf = await mounted("/api/v1/trips", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
});
assert.equal(missingCsrf.status, 403);
const latestTrip = await travelApi.getTrip(trip.id);
await travelApi.deleteTrip(trip.id, latestTrip.revision);

const staleSession = cookies.get("__Host-travel_session");
const csrf = cookies.get("__Host-travel_csrf");
const logout = await mounted("/api/v1/auth/logout", {
  method: "POST", headers: { "X-CSRF-Token": csrf },
});
assert.equal(logout.status, 204);
assert.equal(cookies.has("__Host-travel_session"), false);
cookies.set("__Host-travel_session", staleSession);
const revoked = await mounted("/api/v1/auth/session");
assert.equal((await revoked.json()).authenticated, false);
const privateRevoked = await mounted("/");
assert.equal(privateRevoked.status, 307);
assert.equal(privateRevoked.headers.get("location"), "/sign-in");

console.log(
  process.env.SYNTHETIC_BOOKING_IMPORT_FIXTURE === "1"
    ? "Mounted synthetic sign-in, plaintext and actual PDF booking upload/extraction/review/confirmation, replay/source deletion, typed CRUD, CSRF rejection, logout and private-page protection passed."
    : "Mounted synthetic sign-in, typed CRUD, CSRF rejection, logout and private-page protection passed.",
);
