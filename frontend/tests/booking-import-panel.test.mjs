import assert from "node:assert/strict";
import test from "node:test";

import {
  createEntryError,
  emptyCandidateDraft,
  isDefinitiveUploadFailure,
  normalizeReservationType,
} from "../lib/booking-import-form.mjs";

const candidate = {
  candidate_id: "candidate-12345678",
  reservation_type: "rail",
  provider_name: "Synthetic Rail",
  confirmation_code: "R-1",
  starts_at_date: "2026-10-05",
  starts_at_time: "09:30",
  starts_at_timezone: "+09:00",
  ends_at_date: null,
  ends_at_time: null,
  ends_at_timezone: null,
  uncertain_fields: [],
  current: {
    reservation_type: "train",
    provider_name: null,
    confirmation_code: null,
    starts_at_date: null,
    starts_at_time: null,
    starts_at_timezone: null,
    ends_at_date: null,
    ends_at_time: null,
    ends_at_timezone: null,
    reservation_status: null,
  },
};

test("reopening a corrected candidate honors explicit null clears", () => {
  const draft = emptyCandidateDraft(candidate);
  assert.equal(draft.reservation_type, "train");
  assert.equal(draft.provider_name, "");
  assert.equal(draft.confirmation_code, "");
  assert.equal(draft.starts_at_date, null);
  assert.equal(draft.starts_at_time, null);
  assert.equal(draft.starts_at_timezone, "");
});

test("confirmation requires a chosen tentative or confirmed status and complete required schedule", () => {
  const entry = {
    candidate_id: candidate.candidate_id,
    decision: "create_separate",
    reservation_type: "train",
    reservation_status: null,
    provider_name: "Synthetic Rail",
    starts_at_date: "2026-10-05",
    starts_at_time: "09:30",
    starts_at_timezone: "+09:00",
    ends_at_date: null,
    ends_at_time: null,
    ends_at_timezone: null,
  };
  assert.match(createEntryError(entry, candidate, { acknowledgedUncertainty: false }), /status/);
  entry.reservation_status = "confirmed";
  assert.equal(createEntryError(entry, candidate, { acknowledgedUncertainty: false }), null);
  entry.starts_at_timezone = null;
  assert.match(createEntryError(entry, candidate, { acknowledgedUncertainty: false }), /complete start/);
});

test("review requires an explicit uncertainty acknowledgment", () => {
  const uncertain = { ...candidate, uncertain_fields: ["confirmation_code"] };
  const entry = {
    candidate_id: candidate.candidate_id,
    decision: "create_separate",
    reservation_type: "train",
    reservation_status: "tentative",
    provider_name: "Synthetic Rail",
    starts_at_date: "2026-10-05",
    starts_at_time: "09:30",
    starts_at_timezone: "+09:00",
    ends_at_date: null,
    ends_at_time: null,
    ends_at_timezone: null,
  };
  assert.match(createEntryError(entry, uncertain, { acknowledgedUncertainty: false }), /acknowledge/);
  assert.equal(createEntryError(entry, uncertain, { acknowledgedUncertainty: true }), null);
});

test("reservation aliases normalize before correction or confirmation", () => {
  assert.equal(normalizeReservationType("rail"), "train");
  assert.equal(normalizeReservationType("car"), "car_rental");
  assert.equal(normalizeReservationType("lodging"), "lodging");
  assert.equal(normalizeReservationType("unknown"), null);
});

test("upload reset distinguishes definitive client rejection from uncertain outcomes", () => {
  assert.equal(isDefinitiveUploadFailure(400), true);
  assert.equal(isDefinitiveUploadFailure(422), true);
  assert.equal(isDefinitiveUploadFailure(429), false);
  assert.equal(isDefinitiveUploadFailure(503), false);
  assert.equal(isDefinitiveUploadFailure(null), false);
});
