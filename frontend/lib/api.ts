import { request } from "./api-request.mjs";
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
