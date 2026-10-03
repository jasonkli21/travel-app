export type ItemType = "activity" | "food" | "lodging" | "transport" | "flight" | "note";
export type ItemStatus = "tentative" | "planned" | "booked" | "completed" | "cancelled";

export interface PlaceSummary {
  id: string;
  name: string;
  address: string | null;
  latitude: number | null;
  longitude: number | null;
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
}

export type UpdateItemInput = Partial<CreateItemInput>;

export interface CreatePlaceInput {
  name: string;
  address?: string | null;
  latitude?: number | null;
  longitude?: number | null;
}

export interface ApiErrorPayload {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown> | null;
  };
}

export class ApiError extends Error {
  readonly code: string;
  readonly details: Record<string, unknown> | null;

  constructor(message: string, code = "request_failed", details: Record<string, unknown> | null = null) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.details = details;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init,
      headers: {
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
      cache: "no-store",
    });
  } catch {
    throw new ApiError("The travel API could not be reached. Is the local backend running?", "network_error");
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const payload = (await response.json().catch(() => null)) as ApiErrorPayload | T | null;
  if (!response.ok) {
    const errorPayload = payload as ApiErrorPayload | null;
    throw new ApiError(
      errorPayload?.error?.message ?? `The travel API returned HTTP ${response.status}.`,
      errorPayload?.error?.code ?? "request_failed",
      errorPayload?.error?.details ?? null,
    );
  }
  return payload as T;
}

export const travelApi = {
  listTrips: () => request<TripSummary[]>("/trips"),
  createTrip: (input: CreateTripInput) =>
    request<TripDetail>("/trips", { method: "POST", body: JSON.stringify(input) }),
  getTrip: (tripId: string) => request<TripDetail>(`/trips/${tripId}`),
  updateTrip: (tripId: string, input: UpdateTripInput) =>
    request<TripDetail>(`/trips/${tripId}`, { method: "PATCH", body: JSON.stringify(input) }),
  deleteTrip: (tripId: string) => request<void>(`/trips/${tripId}`, { method: "DELETE" }),
  updateDay: (tripId: string, dayId: string, title: string | null) =>
    request<TripDetail>(`/trips/${tripId}/days/${dayId}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  listPlaces: () => request<PlaceSummary[]>("/places"),
  createPlace: (input: CreatePlaceInput) =>
    request<PlaceSummary>("/places", { method: "POST", body: JSON.stringify(input) }),
  createItem: (tripId: string, dayId: string, input: CreateItemInput) =>
    request<TripDetail>(`/trips/${tripId}/days/${dayId}/items`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  updateItem: (tripId: string, itemId: string, input: UpdateItemInput) =>
    request<TripDetail>(`/trips/${tripId}/items/${itemId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  deleteItem: (tripId: string, itemId: string) =>
    request<void>(`/trips/${tripId}/items/${itemId}`, { method: "DELETE" }),
  moveItem: (tripId: string, itemId: string, destinationDayId: string, position: number) =>
    request<TripDetail>(`/trips/${tripId}/items/${itemId}/move`, {
      method: "POST",
      body: JSON.stringify({ destination_day_id: destinationDayId, position }),
    }),
};
