"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { FormEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  type CreateItemInput,
  type CreateManualSavedPlaceInput,
  type CreatePlaceInput,
  type CreateReservationInput,
  type LogisticsEstimate,
  type LogisticsMode,
  type PlaceSummary,
  type PlaceSearchResult,
  type Reservation,
  type SavedPlace,
  type TripDetail,
  type UpdatePlaceInput,
  type UpdateReservationInput,
  travelApi,
} from "../lib/api";
import TripMap, { type TripMapMarker, type TripMapRoute } from "./trip-map";
import TripResearchPanel from "./trip-research-panel";
import { errorMessage } from "../lib/errors";
import { uncertainMutationError } from "../lib/mutation-outcome.mjs";
import { revisionConflictRecovery } from "../lib/revision-conflict.mjs";
import { safeHttpUrl } from "../lib/urls.mjs";
import ItemForm from "./trip-workspace/item-form";
import DayTitleForm from "./trip-workspace/day-title-form";
import ReservationForm from "./trip-workspace/reservation-form";
import PlaceForm from "./trip-workspace/place-form";
import SavedPlaceNoteForm from "./trip-workspace/saved-place-note-form";


function formatDate(value: string): string {
  // A trip date is a date-only value. Format its UTC components so zones such
  // as UTC+14 cannot display it as the following local calendar date.
  return new Intl.DateTimeFormat("en", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

function reservationSchedule(reservation: Reservation): string {
  if (!reservation.start_date || !reservation.start_time) return "No schedule yet";
  const start = `${reservation.start_date} ${reservation.start_time}`;
  if (!reservation.end_date || !reservation.end_time) return start;
  return `${start} → ${reservation.end_date} ${reservation.end_time}`;
}

function formatDuration(seconds: number): string {
  const totalMinutes = Math.ceil(Math.max(0, seconds) / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours > 0 ? `${hours} hr ${minutes} min` : `${minutes} min`;
}

function formatDistance(meters: number): string {
  return meters < 1000 ? `${Math.round(meters)} m` : `${(meters / 1000).toFixed(1)} km`;
}

function formatAvailableGap(seconds: number): string {
  return seconds < 0
    ? `overlapping by ${formatDuration(-seconds)}`
    : `${formatDuration(seconds)} available`;
}

function PlaceAttribution({ place }: { place: PlaceSummary | null }) {
  if (place?.provider !== "geoapify") return null;
  return (
    <span className="providerAttribution">
      {place.provider_source_attribution ?? "Source attribution unavailable"}
      {place.provider_source_license ? ` · ${place.provider_source_license}` : ""}
      {safeHttpUrl(place.provider_source_url) ? <> · <a href={safeHttpUrl(place.provider_source_url)!} target="_blank" rel="noreferrer">Source</a></> : null}
      {" · "}<a href="https://www.geoapify.com/" target="_blank" rel="noreferrer">Powered by Geoapify</a>
    </span>
  );
}

export default function TripWorkspace({ tripId }: { tripId: string }) {
  const router = useRouter();
  const [trip, setTrip] = useState<TripDetail | null>(null);
  const [places, setPlaces] = useState<PlaceSummary[]>([]);
  const [reservations, setReservations] = useState<Reservation[]>([]);
  const [savedPlaces, setSavedPlaces] = useState<SavedPlace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const mutationInFlight = useRef(false);
  const [stale, setStale] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [editingItem, setEditingItem] = useState<string | null>(null);
  const [addingDay, setAddingDay] = useState<string | null>(null);
  const [editingReservation, setEditingReservation] = useState<string | null>(null);
  const [addingReservation, setAddingReservation] = useState(false);
  const [editingPlace, setEditingPlace] = useState<string | null>(null);
  const [showPlaceForm, setShowPlaceForm] = useState(false);
  const [tripForm, setTripForm] = useState({ title: "", start_date: "", end_date: "", timezone: "UTC" });
  const [showTripEditor, setShowTripEditor] = useState(false);
  const [mapDay, setMapDay] = useState("all");
  const [placeSearchQuery, setPlaceSearchQuery] = useState("");
  const [lastSearchedPlaceQuery, setLastSearchedPlaceQuery] = useState<string | null>(null);
  const [placeSearchResults, setPlaceSearchResults] = useState<PlaceSearchResult[]>([]);
  const searchGeneration = useRef(0);
  const searchInFlight = useRef(false);
  const [placeSearchPending, setPlaceSearchPending] = useState(false);
  const [importingPlaceId, setImportingPlaceId] = useState<string | null>(null);
  const [logisticsMode, setLogisticsMode] = useState<LogisticsMode>("walk");
  const [bufferMinutes, setBufferMinutes] = useState(15);
  const [logistics, setLogistics] = useState<LogisticsEstimate | null>(null);
  const [logisticsPending, setLogisticsPending] = useState(false);
  const [locationError, setLocationError] = useState<string | null>(null);

  const refresh = useCallback(async (): Promise<boolean> => {
    setError(null);
    let nextTrip: TripDetail;
    try {
      nextTrip = await travelApi.getTrip(tripId);
    } catch (nextError) {
      setError(errorMessage(nextError));
      setLoading(false);
      setStale(true);
      return false;
    }

    try {
      const [nextPlaces, nextReservations, nextSavedPlaces] = await Promise.all([
        travelApi.listPlaces(),
        travelApi.listReservations(tripId),
        travelApi.listSavedPlaces(tripId),
      ]);
      setTrip(nextTrip);
      setLogistics(null);
      setMapDay((current) => current === "all" || nextTrip.days.some((day) => day.id === current) ? current : "all");
      setTripForm({ title: nextTrip.title, start_date: nextTrip.start_date, end_date: nextTrip.end_date, timezone: nextTrip.timezone });
      setPlaces(nextPlaces);
      setReservations(nextReservations);
      setSavedPlaces(nextSavedPlaces);
    } catch (nextError) {
      setStale(true);
      setError(`The workspace could not be refreshed: ${errorMessage(nextError)}`);
      setLoading(false);
      return false;
    }
    setStale(false);
    setLoading(false);
    return true;
  }, [tripId]);

  useEffect(() => {
    void Promise.resolve().then(refresh);
  }, [refresh]);

  const run = async (
    key: string,
    operation: () => Promise<void>,
    options: { refreshAfter?: boolean } = {},
  ): Promise<boolean> => {
    if (mutationInFlight.current || stale) return false;
    mutationInFlight.current = true;
    setPending(key);
    setError(null);
    try {
      await operation();
      if (options.refreshAfter !== false && !await refresh()) {
        setError("The change was saved, but the workspace could not be refreshed. Reload before editing again.");
      }
      // A committed write succeeded even if its follow-up read failed. Keeping
      // the create form open invites duplicate reservations/items on retry.
      return true;
    } catch (nextError) {
      mutationError(nextError);
      return false;
    } finally {
      mutationInFlight.current = false;
      setPending(null);
    }
  };

  const mutationError = (nextError: unknown) => {
    const revisionRecovery = revisionConflictRecovery(nextError);
    if (revisionRecovery?.requiresReload) {
      setStale(true);
      setError(revisionRecovery.message);
      return;
    }
    if (uncertainMutationError(nextError)) {
      setStale(true);
      setError("The change could not be confirmed. Reload and check the workspace before submitting again.");
    } else {
      setError(errorMessage(nextError));
    }
  };

  const createPlace = async (name: string) => {
    if (mutationInFlight.current || stale) throw new Error("A workspace change is already pending.");
    mutationInFlight.current = true;
    setPending("quick-place");
    try {
      const place = await travelApi.createPlace({ name });
      setPlaces((current) => [...current, place].sort((left, right) => left.name.localeCompare(right.name)));
      return place;
    } catch (nextError) {
      mutationError(nextError);
      throw nextError;
    } finally {
      mutationInFlight.current = false;
      setPending(null);
    }
  };

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
      const results = await travelApi.searchPlaces(tripId, query);
      if (generation === searchGeneration.current) setPlaceSearchResults(results);
    } catch (nextError) {
      if (generation === searchGeneration.current) setLocationError(errorMessage(nextError));
    } finally {
      searchInFlight.current = false;
      setPlaceSearchPending(false);
    }
  };

  const importSearchResult = async (result: PlaceSearchResult) => {
    if (mutationInFlight.current || stale || !trip) return;
    mutationInFlight.current = true;
    setLocationError(null);
    setPending(`provider-import-${result.provider_place_id}`);
    setImportingPlaceId(result.provider_place_id);
    try {
      await travelApi.importPlace(tripId, {
        provider_place_id: result.provider_place_id,
        name: result.name,
        address: result.address,
        category: result.category,
        latitude: result.latitude,
        longitude: result.longitude,
        provider_source_name: result.provider_source_name,
        provider_source_attribution: result.provider_source_attribution,
        provider_source_license: result.provider_source_license,
        provider_source_url: result.provider_source_url,
        note: null,
      }, trip.revision);
      setPlaceSearchResults((current) =>
        current.filter((candidate) => candidate.provider_place_id !== result.provider_place_id),
      );
      setLastSearchedPlaceQuery(null);
      await refresh();
    } catch (nextError) {
      mutationError(nextError);
      setLocationError(errorMessage(nextError));
    } finally {
      setImportingPlaceId(null);
      mutationInFlight.current = false;
      setPending(null);
    }
  };

  const estimateDayLogistics = async () => {
    if (mutationInFlight.current || stale) return;
    if (mapDay === "all") {
      setLocationError("Choose one itinerary day to estimate travel between its scheduled items.");
      return;
    }
    setLocationError(null);
    mutationInFlight.current = true;
    setPending("logistics-estimate");
    setLogisticsPending(true);
    try {
      const estimate = await travelApi.estimateLogistics(tripId, {
        day_id: mapDay,
        mode: logisticsMode,
        buffer_minutes: bufferMinutes,
      });
      setLogistics(estimate);
    } catch (nextError) {
      setLogistics(null);
      setLocationError(errorMessage(nextError));
    } finally {
      setLogisticsPending(false);
      mutationInFlight.current = false;
      setPending(null);
    }
  };

  const deleteTrip = () => {
    if (!trip) return;
    if (!window.confirm("Permanently delete this trip, reservations, candidates, and itinerary items?")) return;
    void run("trip-delete", async () => {
      await travelApi.deleteTrip(tripId, trip.revision);
      router.push("/");
    }, { refreshAfter: false });
  };

  const dayCountLabel = useMemo(
    () => trip ? `${trip.days.length} ${trip.days.length === 1 ? "day" : "days"}` : "",
    [trip],
  );

  if (loading) return <main className="centerState"><p>Loading trip workspace…</p></main>;
  if (!trip) return <main className="centerState"><p className="formError" role="alert">{error ?? "Trip not found."}</p><Link className="primary linkButton" href="/">Back to trips</Link></main>;

  const confirmedReservations = reservations.filter((reservation) => reservation.status === "confirmed").length;
  const tentativeReservations = reservations.filter((reservation) => reservation.status === "tentative").length;
  const conflictCount = reservations.reduce((total, reservation) => total + reservation.conflicts.length, 0);
  const savedPlaceIds = new Set(savedPlaces.map((savedPlace) => savedPlace.place.id));

  const mapMarkers = (() => {
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
    const selectedDays = mapDay === "all" ? trip.days : trip.days.filter((day) => day.id === mapDay);
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
    if (mapDay === "all") {
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
  })();

  const mapRoutes: TripMapRoute[] = (() => {
    if (!logistics || logistics.day_id !== mapDay) return [];
    return logistics.legs.map((leg) => ({
      id: `${leg.origin_item_id}-${leg.destination_item_id}`,
      geometry: leg.geometry,
      warning: leg.warning,
    }));
  })();

  const saveTrip = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void run("trip", async () => {
      await travelApi.updateTrip(tripId, tripForm, trip.revision);
      setShowTripEditor(false);
    });
  };

  const saveReservation = async (input: CreateReservationInput | UpdateReservationInput, reservationId?: string) => {
    const saved = await run(`reservation-${reservationId ?? "new"}`, async () => {
      if (reservationId) {
        await travelApi.updateReservation(tripId, reservationId, input, trip.revision);
      } else {
        await travelApi.createReservation(tripId, input as CreateReservationInput, trip.revision);
      }
    });
    if (saved) {
      setAddingReservation(false);
      setEditingReservation(null);
    }
    return saved;
  };

  const savePlace = async (input: CreatePlaceInput | UpdatePlaceInput, placeId?: string) => {
    const saved = await run(`place-${placeId ?? "new"}`, async () => {
      if (placeId) {
        const place = places.find((candidate) => candidate.id === placeId);
        if (!place) throw new Error("The place is no longer available in this workspace.");
        await travelApi.updatePlace(placeId, input as UpdatePlaceInput, place.revision);
      }
      else await travelApi.createPlace(input as CreatePlaceInput);
    });
    if (saved) {
      setShowPlaceForm(false);
      setEditingPlace(null);
    }
    return saved;
  };

  const saveResearchCandidate = async (input: CreateManualSavedPlaceInput) => {
    const saved = await run(
      "research-candidate",
      async () => {
        await travelApi.createManualSavedPlace(trip.id, input, trip.revision);
      },
    );
    return saved;
  };

  return (
    <main className="shell">
      <aside className="sidebar">
        <div>
          <p className="eyebrow">PERSONAL TRAVEL</p>
          <Link className="backLink" href="/">← All trips</Link>
          <h1>{trip.title}</h1>
          <p className="muted">{trip.start_date} → {trip.end_date}</p>
        </div>
        <nav className="nav" aria-label="Trip navigation">
          <a href="#overview">Overview</a>
          <a className="active" href="#itinerary">Itinerary</a>
          <a href="#map">Map</a>
          <a href="#research">Research</a>
          <a href="#reservations">Reservations</a>
          <a href="#saved-places">Saved places</a>
        </nav>
        <div className="status"><span className="dot" />Local PostgreSQL planner</div>
      </aside>

      <section className="content" id="overview">
        <header className="topbar">
          <div>
            <p className="eyebrow">{trip.timezone} · {dayCountLabel}</p>
            <h2>Trip overview</h2>
            <p className="muted">Manual changes are authoritative. Reservations and candidates remain useful when AI is offline.</p>
          </div>
          <div className="topActions">
            <button className="secondary" type="button" onClick={() => setShowTripEditor((current) => !current)} disabled={(pending !== null || stale)}>{showTripEditor ? "Close trip editor" : "Edit trip"}</button>
            <button className="danger" type="button" onClick={deleteTrip} disabled={(pending !== null || stale)}>Delete trip</button>
          </div>
        </header>

        {error ? <p className="errorBanner" role="alert">{error}</p> : null}
        {stale ? <button className="secondary" type="button" onClick={() => void refresh()}>Reload workspace</button> : null}

        <div className="overviewPanel" aria-label="Trip summary">
          <div className="overviewStat"><strong>{trip.days.length}</strong><span>days</span></div>
          <div className="overviewStat"><strong>{trip.item_count}</strong><span>itinerary items</span></div>
          <div className="overviewStat"><strong>{confirmedReservations}</strong><span>confirmed anchors</span></div>
          <div className="overviewStat"><strong>{tentativeReservations}</strong><span>tentative bookings</span></div>
          <div className="overviewStat"><strong>{savedPlaces.length}</strong><span>saved candidates</span></div>
          <div className={`overviewStat ${conflictCount > 0 ? "overviewWarning" : ""}`}><strong>{conflictCount}</strong><span>active conflicts</span></div>
        </div>

        {showTripEditor ? (
          <form className="panel tripEditor" onSubmit={saveTrip}>
            <fieldset disabled={(pending !== null || stale)}>
              <div className="formGrid">
                <label>Trip title<input maxLength={200} value={tripForm.title} onChange={(event) => setTripForm({ ...tripForm, title: event.target.value })} required /></label>
                <label>Timezone<input maxLength={64} value={tripForm.timezone} onChange={(event) => setTripForm({ ...tripForm, timezone: event.target.value })} placeholder="America/Los_Angeles" required /></label>
                <label>Start date<input type="date" value={tripForm.start_date} onChange={(event) => setTripForm({ ...tripForm, start_date: event.target.value })} required /></label>
                <label>End date<input type="date" value={tripForm.end_date} onChange={(event) => setTripForm({ ...tripForm, end_date: event.target.value })} required /></label>
              </div>
              <button className="primary" type="submit">{pending === "trip" ? "Saving…" : "Save trip"}</button>
            </fieldset>
          </form>
        ) : null}

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
              <select value={mapDay} disabled={logisticsPending} onChange={(event) => { setMapDay(event.target.value); setLogistics(null); setLocationError(null); }}>
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
              disabled={(pending !== null || stale) || logisticsPending || mapDay === "all"}
            >
              {logisticsPending ? "Estimating…" : "Estimate day logistics"}
            </button>
          </div>
          {mapDay === "all" ? <p className="formHint">Select a day to estimate transfers between its consecutive scheduled items.</p> : null}
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
                <>
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
                </>
              ) : lastSearchedPlaceQuery && lastSearchedPlaceQuery === placeSearchQuery.trim() && !placeSearchPending ? (
                <p className="emptyText">No places found for “{lastSearchedPlaceQuery}”. Try adding a nearby city or area.</p>
              ) : null}
            </form>

            <section className="logisticsPanel" aria-labelledby="logistics-heading">
              <p className="eyebrow">SCHEDULE CHECK</p>
              <h3 id="logistics-heading">Travel between stops</h3>
              {!logistics ? <p className="muted">Choose a day and estimate its route legs. Estimates are on demand and are not saved as trip data.</p> : (
                <>
                  <p className="providerAttribution">Geoapify · {logistics.mode} · Generated {new Date(logistics.generated_at).toLocaleTimeString()}</p>
                  {logistics.legs.length === 0 ? <p className="emptyText">No consecutive scheduled items with coordinates were found for this day.</p> : (
                    <ol className="logisticsLegList">
                      {logistics.legs.map((leg) => (
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

        <TripResearchPanel
          trip={trip}
          pending={(pending !== null || stale)}
          onSaveCandidate={saveResearchCandidate}
        />

        <section className="itinerarySection" id="itinerary">
          <div className="sectionHeading"><div><p className="eyebrow">AUTHORITATIVE PLAN</p><h2>Day-by-day itinerary</h2></div></div>
          <div className="days">
            {trip.days.map((day) => (
              <article className="day" key={day.id}>
                <div className="dayHeading">
                  <div>
                    <p className="date">Day {day.day_index} · {formatDate(day.date)}</p>
                    <DayTitleForm key={`${day.id}-${day.title ?? ""}`} dayId={day.id} title={day.title} pending={pending === `day-${day.id}`} disabled={(pending !== null || stale)} onSave={(title) => run(`day-${day.id}`, async () => { await travelApi.updateDay(trip.id, day.id, title, trip.revision); })} />
                  </div>
                  <button className="secondary compact" type="button" onClick={() => setAddingDay((current) => current === day.id ? null : day.id)} disabled={(pending !== null || stale)}>{addingDay === day.id ? "Close form" : "+ Add item"}</button>
                </div>
                <div className="items">
                  {day.items.length === 0 ? <p className="emptyText">Nothing planned yet.</p> : null}
                  {day.items.map((item, index) => (
                    <div className="itemCard" key={item.id}>
                      <div className="itemSummary">
                        <div className="itemTime">{item.start_time ?? "—"}{item.end_time ? `–${item.end_time}` : ""}</div>
                        <div className="itemBody">
                          <div className="itemTitle"><strong>{item.title}</strong><span className={`badge badge-${item.status}`}>{item.status}</span>{item.reservation ? <span className={`badge badge-${item.reservation.status}`}>{item.reservation.provider_name}</span> : null}{item.reservation && item.reservation.conflict_count > 0 ? <span className="conflictInline">Conflict</span> : null}</div>
                          <p className="itemMeta">{item.item_type}{item.place ? ` · ${item.place.name}` : ""}{item.reservation ? ` · ${item.reservation.confirmation_code ?? "reserved"}` : ""}</p>
                          {item.place ? <PlaceAttribution place={item.place} /> : null}
                          {item.notes ? <p className="itemNotes">{item.notes}</p> : null}
                        </div>
                        <div className="itemActions">
                          <button className="iconButton" type="button" onClick={() => setEditingItem((current) => current === item.id ? null : item.id)} disabled={(pending !== null || stale)} aria-expanded={editingItem === item.id}>{editingItem === item.id ? "Close" : "Edit"}</button>
                          <button className="iconButton" type="button" onClick={() => void run(`move-${item.id}`, () => travelApi.moveItem(trip.id, item.id, day.id, Math.max(index - 1, 0), trip.revision).then(() => undefined))} disabled={(pending !== null || stale) || index === 0} aria-label="Move item up">↑</button>
                          <button className="iconButton" type="button" onClick={() => void run(`move-${item.id}`, () => travelApi.moveItem(trip.id, item.id, day.id, index + 1, trip.revision).then(() => undefined))} disabled={(pending !== null || stale) || index === day.items.length - 1} aria-label="Move item down">↓</button>
                          <select className="moveSelect" defaultValue="" onChange={(event) => { const destination = trip.days.find((candidate) => candidate.id === event.target.value); if (destination) void run(`move-${item.id}`, () => travelApi.moveItem(trip.id, item.id, destination.id, destination.items.length, trip.revision).then(() => undefined)); event.currentTarget.value = ""; }} disabled={(pending !== null || stale)} aria-label="Move item to another day"><option value="">Move to…</option>{trip.days.filter((candidate) => candidate.id !== day.id).map((candidate) => <option key={candidate.id} value={candidate.id}>Day {candidate.day_index}</option>)}</select>
                          <button className="iconButton dangerText" type="button" onClick={() => { if (window.confirm(`Delete ${item.title}?`)) void run(`delete-${item.id}`, async () => { await travelApi.deleteItem(trip.id, item.id, trip.revision); }); }} disabled={(pending !== null || stale)} aria-label={`Delete ${item.title}`}>Delete</button>
                        </div>
                      </div>
                      {editingItem === item.id ? <ItemForm trip={trip} dayId={day.id} places={places} reservations={reservations} initial={item} pending={pending === `item-${item.id}`} disabled={(pending !== null || stale)} onSubmit={async (input) => { const saved = await run(`item-${item.id}`, async () => { await travelApi.updateItem(trip.id, item.id, input, trip.revision); }); if (saved) setEditingItem(null); return saved; }} onCreatePlace={createPlace} onCancel={() => setEditingItem(null)} /> : null}
                    </div>
                  ))}
                </div>
                {addingDay === day.id ? <ItemForm trip={trip} dayId={day.id} places={places} reservations={reservations} pending={pending === `add-${day.id}`} disabled={(pending !== null || stale)} onSubmit={async (input) => { const saved = await run(`add-${day.id}`, async () => { await travelApi.createItem(trip.id, day.id, input as CreateItemInput, trip.revision); }); if (saved) setAddingDay(null); return saved; }} onCreatePlace={createPlace} /> : null}
              </article>
            ))}
          </div>
        </section>

        <section className="reservationSection" id="reservations">
          <div className="sectionHeading">
            <div><p className="eyebrow">BOOKED ANCHORS</p><h2>Reservations</h2><p className="muted">Tentative and confirmed bookings stay separate from optional saved-place candidates.</p></div>
            <button className="primary" type="button" onClick={() => { setAddingReservation((current) => !current); setEditingReservation(null); }} disabled={(pending !== null || stale)}>{addingReservation ? "Close form" : "+ Add reservation"}</button>
          </div>
          {addingReservation ? <ReservationForm places={places} pending={pending === "reservation-new"} disabled={(pending !== null || stale)} onSubmit={(input) => saveReservation(input)} onCancel={() => setAddingReservation(false)} /> : null}
          <div className="reservationList">
            {reservations.length === 0 ? <div className="emptyPanel"><h3>No reservations yet</h3><p className="muted">Add a confirmed anchor or a tentative booking when you have one.</p></div> : reservations.map((reservation) => (
              <article className="reservationCard" key={reservation.id}>
                <div className="reservationHeader">
                  <div><p className="date">{reservation.reservation_type.replace("_", " ")}</p><h3>{reservation.provider_name}</h3></div>
                  <div className="reservationBadges"><span className={`badge badge-${reservation.status}`}>{reservation.status}</span>{reservation.confirmation_code ? <span className="confirmationBadge">{reservation.confirmation_code}</span> : null}</div>
                </div>
                <p className="reservationSchedule">{reservationSchedule(reservation)}</p>
                <div className="reservationDetails">
                  {reservation.place ? <span>Place: {reservation.place.name}</span> : null}
                  {reservation.place ? <PlaceAttribution place={reservation.place} /> : null}
                  {reservation.source_reference ? (safeHttpUrl(reservation.source_reference) ? <a href={safeHttpUrl(reservation.source_reference)!} target="_blank" rel="noreferrer">Open source ↗</a> : <span>{reservation.source_reference}</span>) : null}
                </div>
                {reservation.notes ? <p className="itemNotes">{reservation.notes}</p> : null}
                {reservation.linked_items.length > 0 ? <p className="reservationContext"><strong>Linked itinerary:</strong> {reservation.linked_items.map((item) => `Day ${item.day_index} · ${item.title}`).join("; ")}</p> : <p className="reservationContext muted">Not linked to an itinerary item yet.</p>}
                {reservation.conflicts.length > 0 ? <div className="conflictBox" role="alert"><strong>{reservation.conflicts.length} scheduling {reservation.conflicts.length === 1 ? "conflict" : "conflicts"}</strong><ul>{reservation.conflicts.map((conflict) => <li key={conflict.item_id}>Day {conflict.day_index} · {conflict.title}{conflict.start_time ? ` (${conflict.start_time}${conflict.end_time ? `–${conflict.end_time}` : ""})` : ""}</li>)}</ul><p>{reservation.conflicts[0].reason} Review the plan before applying any change.</p></div> : null}
                <div className="formActions">
                  <button className="secondary compact" type="button" onClick={() => { setEditingReservation((current) => current === reservation.id ? null : reservation.id); setAddingReservation(false); }} disabled={(pending !== null || stale)}>{editingReservation === reservation.id ? "Close editor" : "Edit reservation"}</button>
                  <button className="iconButton dangerText" type="button" onClick={() => { if (window.confirm(`Permanently delete the ${reservation.provider_name} reservation?`)) void run(`delete-reservation-${reservation.id}`, async () => { await travelApi.deleteReservation(trip.id, reservation.id, trip.revision); }); }} disabled={(pending !== null || stale)}>Delete</button>
                </div>
                {editingReservation === reservation.id ? <ReservationForm key={`${reservation.id}-${reservation.updated_at}`} places={places} initial={reservation} pending={pending === `reservation-${reservation.id}`} disabled={(pending !== null || stale)} onSubmit={(input) => saveReservation(input, reservation.id)} onCancel={() => setEditingReservation(null)} /> : null}
              </article>
            ))}
          </div>
        </section>
      </section>

      <aside className="aiPanel" id="saved-places">
        <div>
          <p className="eyebrow">OPTIONAL CANDIDATES</p>
          <h2>Saved places</h2>
          <p className="muted">Save a manual place to this trip without turning it into a booking or itinerary item.</p>
          <button className="secondary" type="button" onClick={() => { setShowPlaceForm((current) => !current); setEditingPlace(null); }} disabled={(pending !== null || stale)}>{showPlaceForm ? "Close place form" : "+ Add place"}</button>
        </div>
        {showPlaceForm ? <PlaceForm pending={pending === "place-new"} disabled={(pending !== null || stale)} onSubmit={(input) => savePlace(input)} onCancel={() => setShowPlaceForm(false)} /> : null}
        {editingPlace ? <PlaceForm key={editingPlace} initial={places.find((place) => place.id === editingPlace)} pending={pending === `place-${editingPlace}`} disabled={(pending !== null || stale)} onSubmit={(input) => savePlace(input, editingPlace)} onCancel={() => setEditingPlace(null)} /> : null}
        <div className="placeGroup">
          <p className="sectionLabel">All places</p>
          <div className="placeList">
            {places.length === 0 ? <p className="emptyText">No places yet.</p> : places.map((place) => {
              const saved = savedPlaceIds.has(place.id);
              return <div className="placeRow" key={place.id}><strong>{place.name}</strong>{place.category || place.address ? <span>{[place.category, place.address].filter(Boolean).join(" · ")}</span> : null}<PlaceAttribution place={place} />{place.phone || place.website_url ? <span>{[place.phone, place.website_url].filter(Boolean).join(" · ")}</span> : null}<div className="placeActions"><button className="iconButton" type="button" onClick={() => { setEditingPlace((current) => current === place.id ? null : place.id); setShowPlaceForm(false); }} disabled={(pending !== null || stale)}>{editingPlace === place.id ? "Close" : "Edit"}</button><button className="iconButton" type="button" onClick={() => void run(`save-place-${place.id}`, async () => { if (!saved) await travelApi.createSavedPlace(trip.id, { place_id: place.id, note: null }, trip.revision); })} disabled={(pending !== null || stale) || saved}>{saved ? "Saved" : "Save"}</button></div></div>;
            })}
          </div>
        </div>
        <div className="placeGroup">
          <p className="sectionLabel">This trip’s candidates</p>
          <div className="placeList">
            {savedPlaces.length === 0 ? <p className="emptyText">No saved candidates yet.</p> : savedPlaces.map((savedPlace) => <div className="placeRow" key={savedPlace.id}><strong>{savedPlace.place.name}</strong>{savedPlace.place.category ? <span>{savedPlace.place.category}</span> : null}<PlaceAttribution place={savedPlace.place} /><SavedPlaceNoteForm key={`${savedPlace.id}-${savedPlace.updated_at}`} savedPlace={savedPlace} pending={pending === `saved-place-${savedPlace.id}`} disabled={(pending !== null || stale)} onSave={(note) => run(`saved-place-${savedPlace.id}`, async () => { await travelApi.updateSavedPlace(trip.id, savedPlace.id, { note }, trip.revision); })} /><button className="iconButton dangerText" type="button" onClick={() => void run(`remove-saved-place-${savedPlace.id}`, async () => { await travelApi.deleteSavedPlace(trip.id, savedPlace.id, trip.revision); })} disabled={(pending !== null || stale)}>Remove candidate</button></div>)}
          </div>
        </div>
        <div className="aiBoundary"><span>Optional research</span><code>travel-api → personal-ai-system</code><p>Research and provider evidence remain separate from these authoritative manual records.</p></div>
      </aside>
    </main>
  );
}
