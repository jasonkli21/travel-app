"use client";

import type { FormEvent } from "react";
import { useMemo, useRef, useState } from "react";
import type {
  LogisticsEstimate,
  LogisticsMode,
  PlaceSearchResult,
  PlaceSummary,
  Reservation,
  SavedPlace,
  TripDetail,
} from "../../lib/api";
import { travelApi } from "../../lib/api";
import { errorMessage } from "../../lib/errors";
import { safeHttpUrl } from "../../lib/urls.mjs";
import TripMap, { type TripMapMarker, type TripMapRoute } from "../trip-map";
import { formatAvailableGap, formatDate, formatDistance, formatDuration } from "./formatters";

type LogisticsInput = { day_id: string; mode: LogisticsMode; buffer_minutes: number };

export default function PlaceMapSection({
  trip,
  places,
  reservations,
  savedPlaces,
  pending,
  stale,
  snapshotVersion,
  onImportSearchResult,
  onEstimateLogistics,
}: {
  trip: TripDetail;
  places: PlaceSummary[];
  reservations: Reservation[];
  savedPlaces: SavedPlace[];
  pending: string | null;
  stale: boolean;
  snapshotVersion: number;
  onImportSearchResult: (result: PlaceSearchResult, onCommitted: () => void) => Promise<void>;
  onEstimateLogistics: (input: LogisticsInput) => Promise<LogisticsEstimate | null>;
}) {
  const [mapDayState, setMapDayState] = useState(() => ({ snapshotVersion, value: "all" }));
  if (mapDayState.snapshotVersion !== snapshotVersion) {
    setMapDayState({
      snapshotVersion,
      value: mapDayState.value === "all" || trip.days.some((day) => day.id === mapDayState.value)
        ? mapDayState.value
        : "all",
    });
  }
  const mapDay = mapDayState.value;
  const setMapDay = (value: string) => setMapDayState({ snapshotVersion, value });
  const [placeSearchQuery, setPlaceSearchQuery] = useState("");
  const [lastSearchedPlaceQuery, setLastSearchedPlaceQuery] = useState<string | null>(null);
  const [placeSearchResults, setPlaceSearchResults] = useState<PlaceSearchResult[]>([]);
  const searchGeneration = useRef(0);
  const searchInFlight = useRef(false);
  const [placeSearchPending, setPlaceSearchPending] = useState(false);
  const [importingPlaceId, setImportingPlaceId] = useState<string | null>(null);
  const [logisticsMode, setLogisticsMode] = useState<LogisticsMode>("walk");
  const [bufferMinutes, setBufferMinutes] = useState(15);
  const [logistics, setLogistics] = useState<{ snapshotVersion: number; estimate: LogisticsEstimate } | null>(null);
  const [logisticsPending, setLogisticsPending] = useState(false);
  const [locationError, setLocationError] = useState<string | null>(null);

  const selectedMapDay = mapDay === "all" || trip.days.some((day) => day.id === mapDay) ? mapDay : "all";
  const currentLogistics = logistics?.snapshotVersion === snapshotVersion ? logistics.estimate : null;

  const searchProviderPlaces = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (searchInFlight.current || stale) return;
    const query = placeSearchQuery.trim();
    if (query.length < 2) {
      setLocationError("Enter at least two characters to search.");
      return;
    }
    const generation = ++searchGeneration.current;
    searchInFlight.current = true;
    setLocationError(null);
    setPlaceSearchPending(true);
    setLastSearchedPlaceQuery(query);
    setPlaceSearchResults([]);
    try {
      const results = await travelApi.searchPlaces(trip.id, query);
      if (generation === searchGeneration.current) setPlaceSearchResults(results);
    } catch (nextError) {
      if (generation === searchGeneration.current) setLocationError(errorMessage(nextError));
    } finally {
      searchInFlight.current = false;
      setPlaceSearchPending(false);
    }
  };

  const importSearchResult = async (result: PlaceSearchResult) => {
    if (stale || pending !== null || importingPlaceId !== null) return;
    setLocationError(null);
    setImportingPlaceId(result.provider_place_id);
    try {
      await onImportSearchResult(result, () => {
        setPlaceSearchResults((current) =>
          current.filter((candidate) => candidate.provider_place_id !== result.provider_place_id),
        );
        setLastSearchedPlaceQuery(null);
      });
    } catch (nextError) {
      setLocationError(errorMessage(nextError));
    } finally {
      setImportingPlaceId(null);
    }
  };

  const estimateDayLogistics = async () => {
    if (stale || pending !== null || logisticsPending) return;
    if (selectedMapDay === "all") {
      setLocationError("Choose one itinerary day to estimate travel between its scheduled items.");
      return;
    }
    setLocationError(null);
    setLogisticsPending(true);
    try {
      const estimate = await onEstimateLogistics({
        day_id: selectedMapDay,
        mode: logisticsMode,
        buffer_minutes: bufferMinutes,
      });
      if (estimate) setLogistics({ snapshotVersion, estimate });
    } catch (nextError) {
      setLogistics(null);
      setLocationError(errorMessage(nextError));
    } finally {
      setLogisticsPending(false);
    }
  };

  const mapMarkers = useMemo(() => {
    const byPlaceId = new Map<string, TripMapMarker>();
    const addPlace = (place: PlaceSummary | null, kind: TripMapMarker["kind"], detail: string) => {
      if (!place || place.latitude === null || place.longitude === null) return;
      const existing = byPlaceId.get(place.id);
      if (existing) {
        const mergedDetail = existing.detail.includes(detail)
          ? existing.detail
          : `${existing.detail}; ${detail}`;
        const mergedKind = existing.kind === "candidate" && kind === "itinerary" ? kind : existing.kind;
        byPlaceId.set(place.id, { ...existing, kind: mergedKind, detail: mergedDetail });
        return;
      }
      byPlaceId.set(place.id, {
        id: place.id,
        name: place.name,
        latitude: place.latitude,
        longitude: place.longitude,
        kind,
        detail,
        sourceAttribution: place.provider_source_attribution,
        sourceLicense: place.provider_source_license,
        sourceUrl: place.provider_source_url,
      });
    };

    const reservationById = new Map(reservations.map((reservation) => [reservation.id, reservation]));
    const selectedDays = selectedMapDay === "all" ? trip.days : trip.days.filter((day) => day.id === selectedMapDay);
    for (const day of selectedDays) {
      for (const item of day.items) {
        if (item.status === "cancelled") continue;
        const reservation = item.reservation ? reservationById.get(item.reservation.id) : undefined;
        const locatedPlace = item.place?.latitude != null && item.place?.longitude != null
          ? item.place : reservation?.status !== "cancelled" ? reservation?.place : null;
        addPlace(locatedPlace ?? null, "itinerary", `Day ${day.day_index} · ${item.title}`);
      }
      for (const reservation of reservations) {
        if (reservation.status === "cancelled" || !reservation.place) continue;
        const coversDay = reservation.start_date !== null
          && reservation.start_date <= day.date
          && (reservation.end_date === null || reservation.end_date >= day.date);
        if (coversDay) {
          addPlace(reservation.place, "reservation", `Day ${day.day_index} · ${reservation.provider_name}`);
        }
      }
    }
    if (selectedMapDay === "all") {
      for (const reservation of reservations) {
        if (reservation.status !== "cancelled") {
          addPlace(reservation.place, "reservation", `Reservation · ${reservation.provider_name}`);
        }
      }
    }
    for (const savedPlace of savedPlaces) {
      addPlace(savedPlace.place, "candidate", "Saved trip candidate");
    }
    for (const result of placeSearchResults) {
      const alreadyImported = places.some((place) => place.provider_place_id === result.provider_place_id);
      if (alreadyImported) continue;
      byPlaceId.set(`search:${result.provider_place_id}`, {
        id: `search:${result.provider_place_id}`,
        name: result.name,
        latitude: result.latitude,
        longitude: result.longitude,
        kind: "search",
        detail: result.category ?? "Search result",
        sourceAttribution: result.provider_source_attribution,
        sourceLicense: result.provider_source_license,
        sourceUrl: result.provider_source_url,
      });
    }
    return [...byPlaceId.values()];
  }, [places, placeSearchResults, reservations, savedPlaces, selectedMapDay, trip.days]);

  const mapRoutes: TripMapRoute[] = currentLogistics?.day_id !== selectedMapDay || !currentLogistics
    ? []
    : currentLogistics.legs.map((leg) => ({
      id: `${leg.origin_item_id}-${leg.destination_item_id}`,
      geometry: leg.geometry,
      warning: leg.warning,
    }));

  return (
    <section className="mapSection" id="map">
      <div className="sectionHeading">
        <div>
          <p className="eyebrow">PLACES AND TRANSFERS</p>
          <h2>Map and logistics</h2>
          <p className="muted">View trip locations, find a place, and check travel time between scheduled stops.</p>
        </div>
      </div>
      <div className="mapControls">
        <label>
          Locations
          <select value={selectedMapDay} disabled={logisticsPending} onChange={(event) => { setMapDay(event.target.value); setLogistics(null); setLocationError(null); }}>
            <option value="all">All trip days</option>
            {trip.days.map((day) => <option key={day.id} value={day.id}>Day {day.day_index} · {formatDate(day.date)}</option>)}
          </select>
        </label>
        <label>
          Travel mode
          <select value={logisticsMode} disabled={logisticsPending} onChange={(event) => { setLogisticsMode(event.target.value as LogisticsMode); setLogistics(null); }}>
            <option value="walk">Walking</option>
            <option value="drive">Driving</option>
            <option value="bicycle">Bicycle</option>
            <option value="transit">Transit estimate</option>
          </select>
        </label>
        <label>
          Transfer buffer
          <select value={bufferMinutes} disabled={logisticsPending} onChange={(event) => { setBufferMinutes(Number(event.target.value)); setLogistics(null); }}>
            {[0, 5, 10, 15, 20, 30].map((minutes) => <option key={minutes} value={minutes}>{minutes} min</option>)}
          </select>
        </label>
        <button
          className="primary"
          type="button"
          onClick={() => void estimateDayLogistics()}
          disabled={(pending !== null || stale) || logisticsPending || selectedMapDay === "all"}
        >
          {logisticsPending ? "Estimating…" : "Estimate day logistics"}
        </button>
      </div>
      {selectedMapDay === "all" ? <p className="formHint">Select a day to estimate transfers between its consecutive scheduled items.</p> : null}
      <TripMap markers={mapMarkers} routes={mapRoutes} label="Trip map and place markers" />
      <p className="formHint">Route times are estimates based on provider map data. They do not include live traffic or guarantee transit schedules.</p>

      {locationError ? <p className="formError locationError" role="alert">{locationError}</p> : null}

      <div className="locationTools">
        <form className="placeSearchPanel" onSubmit={(event) => void searchProviderPlaces(event)}>
          <p className="eyebrow">PLACE SEARCH</p>
          <h3>Find a place to save</h3>
          <label>
            Place or address
            <input
              value={placeSearchQuery}
              onChange={(event) => {
                searchGeneration.current += 1;
                setPlaceSearchQuery(event.target.value);
                setLastSearchedPlaceQuery(null);
                setPlaceSearchResults([]);
              }}
              minLength={2}
              maxLength={160}
              placeholder="Museum, restaurant, or address · include a city"
            />
          </label>
          <button className="secondary" type="submit" disabled={placeSearchPending || (pending !== null || stale) || placeSearchQuery.trim().length < 2}>
            {placeSearchPending ? "Searching…" : "Search places"}
          </button>
          <p className="formHint">Search runs after submission and returns up to ten results. Nothing is saved until you choose a result.</p>
          {placeSearchResults.length > 0 ? (
            <div className="searchResultList">
              {placeSearchResults.map((result) => (
                <article className="searchResult" key={result.provider_place_id}>
                  <div>
                    <strong>{result.name}</strong>
                    <span>{[result.category, result.address].filter(Boolean).join(" · ")}</span>
                    <span className="providerAttribution">
                      {result.provider_source_attribution}
                      {result.provider_source_license ? ` · ${result.provider_source_license}` : ""}
                      {safeHttpUrl(result.provider_source_url) ? <> · <a href={safeHttpUrl(result.provider_source_url)!} target="_blank" rel="noreferrer">Source</a></> : null}
                      {" · "}<a href="https://www.geoapify.com/" target="_blank" rel="noreferrer">Powered by Geoapify</a>
                    </span>
                  </div>
                  <button
                    className="iconButton"
                    type="button"
                    onClick={() => void importSearchResult(result)}
                    disabled={(pending !== null || stale) || importingPlaceId !== null}
                  >
                    {importingPlaceId === result.provider_place_id ? "Saving…" : "Save candidate"}
                  </button>
                </article>
              ))}
            </div>
          ) : lastSearchedPlaceQuery && lastSearchedPlaceQuery === placeSearchQuery.trim() && !placeSearchPending ? (
            <p className="emptyText">No places found for “{lastSearchedPlaceQuery}”. Try adding a nearby city or area.</p>
          ) : null}
        </form>

        <section className="logisticsPanel" aria-labelledby="logistics-heading">
          <p className="eyebrow">SCHEDULE CHECK</p>
          <h3 id="logistics-heading">Travel between stops</h3>
          {!currentLogistics ? <p className="muted">Choose a day and estimate its route legs. Estimates are on demand and are not saved as trip data.</p> : (
            <>
              <p className="providerAttribution">Geoapify · {currentLogistics.mode} · Generated {new Date(currentLogistics.generated_at).toLocaleTimeString()}</p>
              {currentLogistics.legs.length === 0 ? <p className="emptyText">No consecutive scheduled items with coordinates were found for this day.</p> : (
                <ol className="logisticsLegList">
                  {currentLogistics.legs.map((leg) => (
                    <li className={leg.warning ? "logisticsWarning" : "logisticsLeg"} key={`${leg.origin_item_id}-${leg.destination_item_id}`}>
                      <strong>{leg.origin_title} <span aria-hidden="true">→</span> {leg.destination_title}</strong>
                      <span>About {formatDuration(leg.duration_seconds)} · {formatDistance(leg.distance_meters)}</span>
                      <span>{formatAvailableGap(leg.available_gap_seconds)} between scheduled items</span>
                      {leg.warning ? <em>Allow about {formatDuration(leg.duration_seconds + leg.buffer_minutes * 60)} including the selected buffer.</em> : null}
                    </li>
                  ))}
                </ol>
              )}
            </>
          )}
        </section>
      </div>
    </section>
  );
}
