import { request } from "./api-request.mjs";
import type { Reservation, ReservationType, TripDetail } from "./api";

export type BookingImportState =
  | "received"
  | "extracting"
  | "review_ready"
  | "applied"
  | "rejected"
  | "failed"
  | "expired";
export type SourceRetention = "delete_after_confirmation" | "keep_until_expiry";
export type ImportDecision = "create_separate" | "link_existing" | "skip";
export type ReservationStatus = "tentative" | "confirmed";

export interface BookingImportSummary {
  id: string;
  trip_id: string;
  state: BookingImportState;
  source_id: string | null;
  source_state: "ready" | "deleted" | "expired" | "pending";
  media_type: "text/plain" | "application/pdf";
  byte_size: number;
  sha256: string;
  display_filename: string | null;
  review_revision: number;
  retention_choice: SourceRetention;
  extraction_state: BookingImportState;
  extraction_key: string | null;
  upstream_extraction_id: string | null;
  upstream_revision: string | null;
  upstream_result_expires_at: string | null;
  candidates: BookingCandidate[] | null;
  confirmation_outcome: ConfirmationOutcome | null;
  failure_code?: string | null;
  outcome_unknown?: boolean;
  upstream_delete_pending?: boolean;
}

export interface TripLocalTime {
  date: string;
  time: string;
}

export interface BookingCandidate {
  candidate_id: string;
  reservation_type: "flight" | "lodging" | "rail" | "car" | "activity" | "other" | null;
  provider_name: string | null;
  confirmation_code: string | null;
  starts_at_text: string | null;
  starts_at_date: string | null;
  starts_at_time: string | null;
  starts_at_timezone: string | null;
  ends_at_text: string | null;
  ends_at_date: string | null;
  ends_at_time: string | null;
  ends_at_timezone: string | null;
  source_start: number;
  source_end: number;
  source_excerpt: string;
  uncertain_fields: string[];
  starts_at_trip_local: TripLocalTime | null;
  ends_at_trip_local: TripLocalTime | null;
  current: CandidateEdit;
}

export interface CandidateEdit {
  reservation_type?: ReservationType | "rail" | "car" | null;
  reservation_status?: ReservationStatus | null;
  provider_name?: string | null;
  confirmation_code?: string | null;
  starts_at_date?: string | null;
  starts_at_time?: string | null;
  starts_at_timezone?: string | null;
  ends_at_date?: string | null;
  ends_at_time?: string | null;
  ends_at_timezone?: string | null;
  starts_at_trip_local?: TripLocalTime | null;
  ends_at_trip_local?: TripLocalTime | null;
}

export interface DuplicateSuggestion {
  candidate_id: string;
  reservation_id: string;
  reason: "provider_reference" | "provider_schedule";
  choice_required: true;
}

export type BookingImportReview = Omit<BookingImportSummary, "candidates"> & {
  trip_timezone: string;
  candidates: BookingCandidate[];
  duplicate_suggestions: DuplicateSuggestion[];
  confirmation_outcome: ConfirmationOutcome | null;
};

export interface ConfirmationEntry {
  candidate_id: string;
  decision: ImportDecision;
  existing_reservation_id?: string | null;
  place_id?: string | null;
  itinerary_item_id?: string | null;
  provider_name?: string | null;
  confirmation_code?: string | null;
  reservation_type?: ReservationType | null;
  reservation_status?: ReservationStatus | null;
  starts_at_date?: string | null;
  starts_at_time?: string | null;
  starts_at_timezone?: string | null;
  ends_at_date?: string | null;
  ends_at_time?: string | null;
  ends_at_timezone?: string | null;
}

export interface ConfirmationOutcome {
  confirmation_key: string;
  import_id: string;
  trip_id: string;
  trip_revision: number;
  upstream_revision: string;
  outcomes: {
    candidate_id: string;
    outcome: "created" | "linked" | "skipped";
    reservation_id?: string;
    itinerary_item_id?: string | null;
  }[];
}

export interface ConfirmBookingsInput {
  confirmation_key: string;
  expected_trip_revision: number;
  expected_import_revision: number;
  entries: ConfirmationEntry[];
}

export interface ImportEditInput {
  candidate_id: string;
  reservation_type?: ReservationType | "rail" | "car" | null;
  reservation_status?: ReservationStatus | null;
  provider_name?: string | null;
  confirmation_code?: string | null;
  starts_at_date?: string | null;
  starts_at_time?: string | null;
  starts_at_timezone?: string | null;
  ends_at_date?: string | null;
  ends_at_time?: string | null;
  ends_at_timezone?: string | null;
}

export interface ReviewWorkspace {
  trip: TripDetail;
  reservations: Reservation[];
}

export interface DeletionRetryResult {
  attempted: number;
  deleted: number;
  failed: number;
  pending: number;
}

export const bookingImportsEnabled =
  process.env.NEXT_PUBLIC_PRIVATE_IMPORTS_ENABLED === "true";

export const bookingImportApi = {
  list: (tripId: string) =>
    request<BookingImportSummary[]>(`/trips/${tripId}/imports`),
  get: (tripId: string, importId: string) =>
    request<BookingImportReview>(`/trips/${tripId}/imports/${importId}`),
  upload: (
    tripId: string,
    body: Blob | string,
    mediaType: "text/plain" | "application/pdf",
    requestKey: string,
    retention: SourceRetention,
    filename?: string,
  ) =>
    request<BookingImportSummary>(`/trips/${tripId}/imports`, {
      method: "POST",
      headers: {
        "Content-Type": mediaType,
        "X-Import-Request-Key": requestKey,
        "X-Source-Retention": retention,
        ...(filename ? { "X-Source-Filename": filename.slice(0, 512) } : {}),
      },
      body,
    }),
  extract: (tripId: string, importId: string) =>
    request<BookingImportReview>(`/trips/${tripId}/imports/${importId}/extract`, {
      method: "POST",
    }),
  saveEdits: (
    tripId: string,
    importId: string,
    expectedImportRevision: number,
    edits: ImportEditInput[],
  ) =>
    request<BookingImportReview>(`/trips/${tripId}/imports/${importId}/review`, {
      method: "PATCH",
      body: JSON.stringify({ expected_import_revision: expectedImportRevision, edits }),
    }),
  confirm: (tripId: string, importId: string, input: ConfirmBookingsInput) =>
    request<ConfirmationOutcome>(`/trips/${tripId}/imports/${importId}/confirm`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  reject: (tripId: string, importId: string) =>
    request<BookingImportReview>(`/trips/${tripId}/imports/${importId}/reject`, {
      method: "POST",
    }),
  deleteSource: (tripId: string, importId: string) =>
    request<void>(`/trips/${tripId}/imports/${importId}/source`, { method: "DELETE" }),
  retryPendingDeletions: () =>
    request<DeletionRetryResult>("/private-import-deletion-intents/retry", { method: "POST" }),
  sourceUrl: (tripId: string, importId: string) =>
    `/api/v1/trips/${tripId}/imports/${importId}/source`,
};
