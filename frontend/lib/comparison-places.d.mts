import type { PlaceSummary, Reservation, SavedPlace, TripDetail } from "./api";

export function uniqueComparisonPlaces(
  trip: TripDetail,
  savedPlaces: SavedPlace[],
  reservations: Reservation[],
): PlaceSummary[];
