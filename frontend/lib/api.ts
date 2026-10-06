import { download as downloadRequest, request } from "./api-request.mjs";
export { ApiError } from "./api-request.mjs";

export type ItemType = "activity" | "food" | "lodging" | "transport" | "flight" | "note";
export type ItemStatus = "tentative" | "planned" | "booked" | "completed" | "cancelled";
export type ReservationType = "lodging" | "flight" | "train" | "car_rental" | "activity" | "dining" | "other";
export type ReservationStatus = "tentative" | "confirmed" | "cancelled";
export type LogisticsMode = "walk" | "drive" | "bicycle" | "transit";
export type ResearchFreshness = "general" | "current";
export type ResearchState = "pending" | "running" | "completed" | "insufficient" | "failed" | "expired";

export interface PlaceSummary {
  id: string;
  revision: number;
  name: string;
  address: string | null;
  category: string | null;
  phone: string | null;
  website_url: string | null;
  latitude: number | null;
  longitude: number | null;
  provider: string | null;
  provider_place_id: string | null;
  provider_source_name: string | null;
  provider_source_attribution: string | null;
  provider_source_license: string | null;
  provider_source_url: string | null;
}

export interface PlaceSearchResult {
  provider: "geoapify";
  provider_place_id: string;
  name: string;
  address: string | null;
  category: string | null;
  latitude: number;
  longitude: number;
  provider_source_name: string;
  provider_source_attribution: string;
  provider_source_license: string | null;
  provider_source_url: string | null;
}

export interface ImportPlaceInput {
  provider_place_id: string;
  name: string;
  address: string | null;
  category: string | null;
  latitude: number;
  longitude: number;
  provider_source_name: string;
  provider_source_attribution: string;
  provider_source_license: string | null;
  provider_source_url: string | null;
  note: string | null;
}

export interface LogisticsLeg {
  origin_item_id: string;
  origin_title: string;
  destination_item_id: string;
  destination_title: string;
  duration_seconds: number;
  distance_meters: number;
  available_gap_seconds: number;
  buffer_minutes: number;
  warning: boolean;
  geometry: [number, number][];
}

export interface LogisticsEstimate {
  provider: "geoapify";
  day_id: string;
  mode: LogisticsMode;
  buffer_minutes: number;
  generated_at: string;
  legs: LogisticsLeg[];
}

export interface ReservationSummary {
  id: string;
  reservation_type: ReservationType;
  status: ReservationStatus;
  provider_name: string;
  confirmation_code: string | null;
  conflict_count: number;
}

export interface ItineraryItem {
  id: string;
  item_type: ItemType;
  title: string;
  notes: string | null;
  start_time: string | null;
  end_time: string | null;
  sort_order: number;
  status: ItemStatus;
  place: PlaceSummary | null;
  reservation: ReservationSummary | null;
}

export interface TripDay {
  id: string;
  day_index: number;
  date: string;
  title: string | null;
  items: ItineraryItem[];
}

export interface TripSummary {
  id: string;
  revision: number;
  title: string;
  start_date: string;
  end_date: string;
  timezone: string;
  day_count: number;
  item_count: number;
  created_at: string;
  updated_at: string;
}

export interface TripDetail extends TripSummary {
  days: TripDay[];
}

export interface ReservationLinkedItem {
  id: string;
  title: string;
  day_id: string;
  day_index: number;
  date: string;
}

export interface ReservationConflict {
  item_id: string;
  day_id: string;
  day_index: number;
  date: string;
  title: string;
  start_time: string | null;
  end_time: string | null;
  reason: string;
}

export interface Reservation {
  id: string;
  reservation_type: ReservationType;
  status: ReservationStatus;
  provider_name: string;
  confirmation_code: string | null;
  start_date: string | null;
  start_time: string | null;
  end_date: string | null;
  end_time: string | null;
  place: PlaceSummary | null;
  source_reference: string | null;
  notes: string | null;
  linked_items: ReservationLinkedItem[];
  conflicts: ReservationConflict[];
  created_at: string;
  updated_at: string;
}

export interface SavedPlace {
  id: string;
  note: string | null;
  place: PlaceSummary;
  created_at: string;
  updated_at: string;
}

export interface ResearchCitation {
  number: number;
  evidence_id: string;
  source_observation_id: string;
  url: string;
  title: string | null;
  observed_at: string;
  expires_at: string;
}

export interface TripResearchResult {
  schema_version: "trip-research-v1";
  session_id: string;
  state: ResearchState;
  answer: string | null;
  failure_code: string | null;
  expires_at: string;
  citations: ResearchCitation[];
}

export type TravelComparisonCategory = "food" | "activity" | "neighborhood" | "day_trip";
export type TravelComparisonOutcome = "pass" | "fail" | "unknown";
export type TravelComparisonState =
  | "recommended"
  | "eligible_unranked"
  | "research_needed"
  | "no_verified_match"
  | "insufficient"
  | "expired";

export interface TravelComparisonRequest {
  category: TravelComparisonCategory;
  query: string;
  reference_place_id: string;
  reference_place_revision: number;
  reference_latitude: number;
  reference_longitude: number;
  trip_revision: number;
  radius_km: number;
  max_results: number;
  idempotency_key: string;
}

export interface TravelComparisonSource {
  evidence_id: string;
  source_observation_id: string;
  provider: string;
  url: string;
  title: string | null;
  attribution: string;
  policy_url: string | null;
  observed_at: string;
  expires_at: string;
}

export interface TravelComparisonConstraint {
  name: "category" | "distance";
  label: string;
  outcome: TravelComparisonOutcome;
  detail: string;
}

export interface TravelComparisonCandidate {
  candidate_id: string;
  name: string;
  category: TravelComparisonCategory;
  address: string | null;
  place_type: string | null;
  latitude: number | null;
  longitude: number | null;
  distance_km: number | null;
  eligible: boolean;
  rank: number | null;
  score: number | null;
  exclusion_reasons: string[];
  constraints: TravelComparisonConstraint[];
  sources: TravelComparisonSource[];
}

export interface TravelComparisonResult {
  schema_version: "travel-research-comparison-v1";
  comparison_id: string;
  category: TravelComparisonCategory;
  state: TravelComparisonState;
  trip_revision: number;
  reference_place_id: string;
  reference_place_revision: number;
  reference_place_name: string;
  radius_km: number;
  generated_at: string;
  expires_at: string | null;
  candidates: TravelComparisonCandidate[];
}

export interface SaveTravelComparisonCandidateInput {
  name: string;
  address: string | null;
  category: string | null;
  note: string | null;
  trip_revision: number;
  reference_place_id: string;
  reference_place_revision: number;
}

export type ProposalState =
  | "generating"
  | "outcome_unknown"
  | "ready"
  | "stale"
  | "expired"
  | "failed"
  | "applied"
  | "rejected";

export type ProposalOperation =
  | {
      kind: "add_item";
      day_handle: string;
      candidate_handle: string;
      item_type: ItemType;
      position: number;
      start_time?: string | null;
      end_time?: string | null;
    }
  | { kind: "move_item"; item_handle: string; day_handle: string; position: number }
  | {
      kind: "set_item_times";
      item_handle: string;
      start_time?: string | null;
      end_time?: string | null;
    }
  | { kind: "remove_item"; item_handle: string };

export interface ProposalPreviewItem {
  handle: string;
  title: string;
  item_type: ItemType;
  status: ItemStatus;
  sort_order: number;
  start_time: string | null;
  end_time: string | null;
  place_handle: string | null;
  reservation_handle: string | null;
}

export interface ProposalPreviewDay {
  handle: string;
  day_index: number;
  date: string;
  title: string | null;
  items: ProposalPreviewItem[];
}

export type ProposalDiffItem =
  | (Pick<ProposalPreviewItem, "title" | "item_type" | "start_time" | "end_time"> & {
      day_index: number;
      date: string;
      sort_order: number;
    })
  | null;

export interface ProposalPreviewDiff {
  operation_index: number;
  kind: ProposalOperation["kind"];
  before: ProposalDiffItem;
  after: ProposalDiffItem;
}

export interface ProposalPreviewWarning {
  code: string;
  day_handle: string;
  item_handle: string;
  reservation_handle: string;
  message: string;
}

export interface ProposalPreview {
  trip_handle: string;
  base_trip_revision: number;
  place_revisions: { place_id: string; handle: string; revision: number }[];
  operation_count: number;
  before: ProposalPreviewDay[];
  after: ProposalPreviewDay[];
  diff: ProposalPreviewDiff[];
  warnings: ProposalPreviewWarning[];
}

export interface ProposalCitation {
  evidence_handle: string;
  url: string;
  title: string | null;
  observed_at: string;
  expires_at: string;
}

export interface ProposalOperationSupport {
  operation_index: number;
  evidence_handles: string[];
}

export interface ProposalDetail {
  proposal_id: string;
  state: ProposalState;
  lifecycle_state: Exclude<ProposalState, "stale" | "expired">;
  support_mode: "context_only" | "research_evidence";
  upstream_revision: string;
  trip_handle: string;
  created_at: string;
  expires_at: string | null;
  base_trip_revision: number;
  current_trip_revision: number | null;
  base_place_revisions: { place_id: string; revision: number }[];
  current_place_revisions: { place_id: string; revision: number | null }[];
  operations: ProposalOperation[];
  operation_support: ProposalOperationSupport[];
  citations: ProposalCitation[];
  preview: ProposalPreview | null;
  applied_outcome: ProposalApplyOutcome | null;
  failure_code: string | null;
}

export interface ProposalApplyOutcome {
  proposal_id: string;
  state: "applied";
  applied_revision: number;
  applied_at: string;
  preview: ProposalPreview;
}

export interface CreateProposalInput {
  idempotency_key: string;
  instruction: string;
  removable_item_ids: string[];
  research_session_ids?: string[];
}

export interface CreateTripInput {
  title: string;
  start_date: string;
  end_date: string;
  timezone: string;
}

export interface UpdateTripInput {
  title?: string;
  start_date?: string;
  end_date?: string;
  timezone?: string;
}

export interface CreateItemInput {
  item_type: ItemType;
  title: string;
  notes: string | null;
  start_time: string | null;
  end_time: string | null;
  status: ItemStatus;
  place_id: string | null;
  reservation_id: string | null;
}

export type UpdateItemInput = Partial<CreateItemInput>;

export interface CreatePlaceInput {
  name: string;
  address?: string | null;
  category?: string | null;
  phone?: string | null;
  website_url?: string | null;
  latitude?: number | null;
  longitude?: number | null;
}

export interface UpdatePlaceInput {
  name?: string | null;
  address?: string | null;
  category?: string | null;
  phone?: string | null;
  website_url?: string | null;
  latitude?: number | null;
  longitude?: number | null;
}

export interface CreateReservationInput {
  reservation_type: ReservationType;
  status: ReservationStatus;
  provider_name: string;
  confirmation_code: string | null;
  start_date: string | null;
  start_time: string | null;
  end_date: string | null;
  end_time: string | null;
  place_id: string | null;
  source_reference: string | null;
  notes: string | null;
}

export type UpdateReservationInput = Partial<CreateReservationInput>;

export interface CreateSavedPlaceInput {
  place_id: string;
  note: string | null;
}

export interface CreateManualSavedPlaceInput extends CreatePlaceInput {
  note?: string | null;
}

export interface UpdateSavedPlaceInput {
  note: string | null;
}

export interface ApiErrorPayload {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown> | null;
  };
}

export type AttachmentMediaType = "text/plain" | "application/pdf" | "image/jpeg" | "image/png";
export type AttachmentState = "pending" | "ready" | "deleting" | "missing";

export interface TripAttachment {
  id: string;
  trip_id: string | null;
  reservation_id: string | null;
  display_filename: string;
  media_type: AttachmentMediaType;
  byte_size: number;
  state: AttachmentState;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
  trip_revision: number;
  download_available: boolean;
}

export type TripExportFormat = "ics" | "html" | "json";

export interface TripExportInput {
  format: TripExportFormat;
  start_date?: string;
  end_date?: string;
  include_private_fields: boolean;
  include_documents: boolean;
  include_linked_reservations: boolean;
}

export const tripAttachmentsEnabled =
  process.env.NEXT_PUBLIC_PRIVATE_ATTACHMENTS_ENABLED === "true";

const expectedRevision = (revision: number) => ({
  "X-Expected-Revision": String(revision),
});

export const travelApi = {
  listTrips: () => request<TripSummary[]>("/trips"),
  createTrip: (input: CreateTripInput) =>
    request<TripDetail>("/trips", { method: "POST", body: JSON.stringify(input) }),
  getTrip: (tripId: string) => request<TripDetail>(`/trips/${tripId}`),
  updateTrip: (tripId: string, input: UpdateTripInput, revision: number) =>
    request<TripDetail>(`/trips/${tripId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
      headers: expectedRevision(revision),
    }),
  deleteTrip: (tripId: string, revision: number) =>
    request<void>(`/trips/${tripId}`, { method: "DELETE", headers: expectedRevision(revision) }),
  updateDay: (tripId: string, dayId: string, title: string | null, revision: number) =>
    request<TripDetail>(`/trips/${tripId}/days/${dayId}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
      headers: expectedRevision(revision),
    }),
  listPlaces: () => request<PlaceSummary[]>("/places"),
  searchPlaces: (tripId: string, query: string, limit = 10) =>
    request<PlaceSearchResult[]>(
      `/trips/${tripId}/places/search?q=${encodeURIComponent(query)}&limit=${limit}`,
    ),
  importPlace: (tripId: string, input: ImportPlaceInput, revision: number) =>
    request<SavedPlace>(`/trips/${tripId}/saved-places/import`, {
      method: "POST",
      body: JSON.stringify(input),
      headers: expectedRevision(revision),
    }),
  estimateLogistics: (
    tripId: string,
    input: { day_id: string; mode: LogisticsMode; buffer_minutes: number },
  ) =>
    request<LogisticsEstimate>(`/trips/${tripId}/logistics/estimate`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  listAttachments: (tripId: string) =>
    request<TripAttachment[]>(`/trips/${tripId}/attachments`),
  uploadAttachment: (
    tripId: string,
    file: File,
    revision: number,
    requestKey: string,
    reservationId: string | null,
  ) => {
    const safeFilename = file.name.replace(/[^A-Za-z0-9 ._-]/g, "_").slice(0, 512);
    return request<TripAttachment>(`/trips/${tripId}/attachments`, {
      method: "POST",
      headers: {
        "Content-Type": file.type,
        "X-Expected-Revision": String(revision),
        "X-Attachment-Request-Key": requestKey,
        "X-Attachment-Filename": safeFilename,
        ...(reservationId ? { "X-Attachment-Reservation-ID": reservationId } : {}),
      },
      body: file,
    });
  },
  updateAttachment: (
    tripId: string,
    attachmentId: string,
    input: { display_filename: string | null; reservation_id: string | null },
    revision: number,
  ) => request<TripAttachment>(`/trips/${tripId}/attachments/${attachmentId}`, {
    method: "PATCH",
    body: JSON.stringify(input),
    headers: expectedRevision(revision),
  }),
  deleteAttachment: (tripId: string, attachmentId: string, revision: number) =>
    request<void>(`/trips/${tripId}/attachments/${attachmentId}`, {
      method: "DELETE",
      headers: expectedRevision(revision),
    }),
  attachmentDownloadUrl: (tripId: string, attachmentId: string) =>
    `/api/v1/trips/${tripId}/attachments/${attachmentId}/download`,
  createTripExport: (tripId: string, input: TripExportInput) =>
    downloadRequest(`/trips/${tripId}/exports`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  researchTripDay: (
    tripId: string,
    input: {
      day_id: string;
      question: string;
      freshness: ResearchFreshness;
      idempotency_key: string;
    },
  ) => request<TripResearchResult>(`/trips/${tripId}/research`, {
    method: "POST",
    body: JSON.stringify(input),
  }),
  compareTripPlaces: (tripId: string, input: TravelComparisonRequest) =>
    request<TravelComparisonResult>(`/trips/${tripId}/research/compare`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  saveTravelComparisonCandidate: (
    tripId: string,
    comparisonId: string,
    candidateId: string,
    input: SaveTravelComparisonCandidateInput,
    revision: number,
  ) => request<SavedPlace>(
    `/trips/${tripId}/research/comparisons/${comparisonId}/candidates/${candidateId}/save`,
    {
      method: "POST",
      body: JSON.stringify(input),
      headers: expectedRevision(revision),
    },
  ),
  createProposal: (tripId: string, input: CreateProposalInput, revision: number) =>
    request<ProposalDetail>(`/trips/${tripId}/proposals`, {
      method: "POST",
      body: JSON.stringify(input),
      headers: expectedRevision(revision),
    }),
  getProposal: (tripId: string, proposalId: string) =>
    request<ProposalDetail>(`/trips/${tripId}/proposals/${proposalId}`),
  getProposalByKey: (tripId: string, idempotencyKey: string) =>
    request<ProposalDetail>(
      `/trips/${tripId}/proposals/by-key/${idempotencyKey}`,
    ),
  applyProposal: (tripId: string, proposalId: string, revision: number) =>
    request<ProposalApplyOutcome>(`/trips/${tripId}/proposals/${proposalId}/apply`, {
      method: "POST",
      headers: expectedRevision(revision),
    }),
  rejectProposal: (tripId: string, proposalId: string) =>
    request<ProposalDetail>(`/trips/${tripId}/proposals/${proposalId}/reject`, {
      method: "POST",
    }),
  createPlace: (input: CreatePlaceInput) =>
    request<PlaceSummary>("/places", { method: "POST", body: JSON.stringify(input) }),
  updatePlace: (placeId: string, input: UpdatePlaceInput, revision: number) =>
    request<PlaceSummary>(`/places/${placeId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
      headers: expectedRevision(revision),
    }),
  listReservations: (tripId: string) => request<Reservation[]>(`/trips/${tripId}/reservations`),
  createReservation: (tripId: string, input: CreateReservationInput, revision: number) =>
    request<Reservation>(`/trips/${tripId}/reservations`, {
      method: "POST",
      body: JSON.stringify(input),
      headers: expectedRevision(revision),
    }),
  updateReservation: (
    tripId: string,
    reservationId: string,
    input: UpdateReservationInput,
    revision: number,
  ) =>
    request<Reservation>(`/trips/${tripId}/reservations/${reservationId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
      headers: expectedRevision(revision),
    }),
  deleteReservation: (tripId: string, reservationId: string, revision: number) =>
    request<void>(`/trips/${tripId}/reservations/${reservationId}`, {
      method: "DELETE",
      headers: expectedRevision(revision),
    }),
  listSavedPlaces: (tripId: string) => request<SavedPlace[]>(`/trips/${tripId}/saved-places`),
  createSavedPlace: (tripId: string, input: CreateSavedPlaceInput, revision: number) =>
    request<SavedPlace>(`/trips/${tripId}/saved-places`, {
      method: "POST",
      body: JSON.stringify(input),
      headers: expectedRevision(revision),
    }),
  createManualSavedPlace: (
    tripId: string,
    input: CreateManualSavedPlaceInput,
    revision: number,
  ) =>
    request<SavedPlace>(`/trips/${tripId}/saved-places/manual`, {
      method: "POST",
      body: JSON.stringify(input),
      headers: expectedRevision(revision),
    }),
  updateSavedPlace: (
    tripId: string,
    savedPlaceId: string,
    input: UpdateSavedPlaceInput,
    revision: number,
  ) =>
    request<SavedPlace>(`/trips/${tripId}/saved-places/${savedPlaceId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
      headers: expectedRevision(revision),
    }),
  deleteSavedPlace: (tripId: string, savedPlaceId: string, revision: number) =>
    request<void>(`/trips/${tripId}/saved-places/${savedPlaceId}`, {
      method: "DELETE",
      headers: expectedRevision(revision),
    }),
  createItem: (tripId: string, dayId: string, input: CreateItemInput, revision: number) =>
    request<TripDetail>(`/trips/${tripId}/days/${dayId}/items`, {
      method: "POST",
      body: JSON.stringify(input),
      headers: expectedRevision(revision),
    }),
  updateItem: (tripId: string, itemId: string, input: UpdateItemInput, revision: number) =>
    request<TripDetail>(`/trips/${tripId}/items/${itemId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
      headers: expectedRevision(revision),
    }),
  deleteItem: (tripId: string, itemId: string, revision: number) =>
    request<void>(`/trips/${tripId}/items/${itemId}`, {
      method: "DELETE",
      headers: expectedRevision(revision),
    }),
  moveItem: (
    tripId: string,
    itemId: string,
    destinationDayId: string,
    position: number,
    revision: number,
  ) =>
    request<TripDetail>(`/trips/${tripId}/items/${itemId}/move`, {
      method: "POST",
      body: JSON.stringify({ destination_day_id: destinationDayId, position }),
      headers: expectedRevision(revision),
    }),
};
