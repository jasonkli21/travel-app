"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  type LogisticsEstimate,
  type LogisticsMode,
  type PlaceSummary,
  type Reservation,
  type TripAttachment,
  type TripDetail,
  travelApi,
  tripAttachmentsEnabled,
} from "../lib/api";
import { errorMessage } from "../lib/errors";
import { safeHttpUrl } from "../lib/urls.mjs";

function displayDate(value: string): string {
  return new Intl.DateTimeFormat("en", {
    weekday: "long",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

function formatDuration(seconds: number): string {
  const minutes = Math.ceil(Math.max(0, seconds) / 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)} hr ${minutes % 60} min` : `${minutes} min`;
}

function scheduleText(start: string | null, end: string | null, flexible = "Flexible") {
  if (!start && !end) return flexible;
  if (!start) return end;
  if (!end || end === start) return start;
  return `${start}–${end}`;
}

function reservationSchedule(reservation: Reservation): string {
  const start = [reservation.start_date, reservation.start_time].filter(Boolean).join(" ");
  const end = [reservation.end_date, reservation.end_time].filter(Boolean).join(" ");
  if (!start && !end) return "Schedule not set";
  if (!start) return end;
  if (!end || end === start) return start;
  return `${start} – ${end}`;
}

function PlaceSource({ place }: { place: PlaceSummary | null }) {
  if (!place) return null;
  const attribution = place.provider_source_attribution
    ?? (place.provider === "osm_nominatim" ? "© OpenStreetMap contributors" : null)
    ?? (place.provider === "geoapify" ? "Powered by Geoapify" : null)
    ?? place.provider_source_name;
  if (!attribution && !place.provider_source_license && !place.provider_source_url) {
    return null;
  }
  const url = safeHttpUrl(place.provider_source_url);
  return (
    <span className="providerAttribution">
      {[attribution, place.provider_source_license].filter(Boolean).join(" · ")}
      {url ? <> · <a href={url} target="_blank" rel="noreferrer">Source</a></> : null}
    </span>
  );
}

export default function TripTravelMode({ tripId }: { tripId: string }) {
  const [trip, setTrip] = useState<TripDetail | null>(null);
  const [reservations, setReservations] = useState<Reservation[]>([]);
  const [attachments, setAttachments] = useState<TripAttachment[]>([]);
  const [attachmentsAvailable, setAttachmentsAvailable] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<LogisticsMode>("walk");
  const [buffer, setBuffer] = useState(15);
  const [pendingDay, setPendingDay] = useState<string | null>(null);
  const [estimates, setEstimates] = useState<Record<string, LogisticsEstimate>>({});
  const refreshGeneration = useRef(0);
  const estimateGeneration = useRef(0);
  const tripSnapshot = useRef<TripDetail | null>(null);

  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current;
    estimateGeneration.current += 1;
    setPendingDay(null);
    try {
      const projection = await travelApi.getTravelMode(tripId);
      if (generation !== refreshGeneration.current) return false;
      tripSnapshot.current = projection.trip;
      setTrip(projection.trip);
      setReservations(projection.reservations);
      setAttachments(projection.attachments);
      setAttachmentsAvailable(projection.attachments_available);
      setEstimates({});
      setError(null);
      return true;
    } catch (nextError) {
      if (generation !== refreshGeneration.current) return false;
      // Keep the last visible projection on screen, but say clearly that it is
      // not a background-refreshed or offline-synchronized copy.
      setError(errorMessage(nextError));
    } finally {
      if (generation === refreshGeneration.current) setLoading(false);
    }
  }, [tripId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => {
      window.clearTimeout(timer);
      refreshGeneration.current += 1;
      estimateGeneration.current += 1;
    };
  }, [refresh]);

  const estimate = async (dayId: string) => {
    const generation = ++estimateGeneration.current;
    const revision = trip?.revision;
    const requestedMode = mode;
    const requestedBuffer = buffer;
    setPendingDay(dayId);
    setError(null);
    try {
      const result = await travelApi.estimateLogistics(tripId, {
        day_id: dayId,
        mode: requestedMode,
        buffer_minutes: requestedBuffer,
      });
      if (
        generation === estimateGeneration.current
        && tripSnapshot.current?.revision === revision
        && result.day_id === dayId
        && result.mode === requestedMode
        && result.buffer_minutes === requestedBuffer
      ) {
        setEstimates((current) => ({ ...current, [dayId]: result }));
      }
    } catch (nextError) {
      if (generation === estimateGeneration.current) setError(errorMessage(nextError));
    } finally {
      if (generation === estimateGeneration.current) setPendingDay(null);
    }
  };

  if (loading && !trip) {
    return <main className="travelModeShell"><p>Loading trip travel view…</p></main>;
  }
  if (!trip) {
    return (
      <main className="travelModeShell">
        <p className="formError" role="alert">{error ?? "Trip not found."}</p>
        <Link className="secondary linkButton" href={`/trips/${tripId}`}>Back to trip</Link>
      </main>
    );
  }

  const reservationById = new Map(reservations.map((reservation) => [reservation.id, reservation]));
  const visibleAnchorIds = new Set(
    trip.days.flatMap((day) =>
      day.items
        .filter((item) => item.status !== "cancelled" && item.reservation !== null)
        .map((item) => item.reservation!.id)
        .filter((id) => reservationById.get(id)?.status !== "cancelled"),
    ),
  );
  const reservationsWithoutVisibleAnchor = reservations.filter(
    (reservation) => reservation.status !== "cancelled" && !visibleAnchorIds.has(reservation.id),
  );
  const attachmentByReservation = new Map<string, TripAttachment[]>();
  for (const attachment of attachments) {
    if (!attachment.reservation_id) continue;
    const rows = attachmentByReservation.get(attachment.reservation_id) ?? [];
    rows.push(attachment);
    attachmentByReservation.set(attachment.reservation_id, rows);
  }

  return (
    <main className="travelModeShell">
      <header className="travelModeHeader">
        <div>
          <Link className="backLink" href={`/trips/${trip.id}`}>← Trip workspace</Link>
          <p className="eyebrow">READ-FOCUSED TRAVEL MODE</p>
          <h1>{trip.title}</h1>
          <p className="muted">{trip.start_date} → {trip.end_date} · {trip.timezone}</p>
        </div>
        <Link className="primary linkButton" href={`/trips/${trip.id}#exports`}>Download offline snapshot</Link>
      </header>
      <p className="travelModeNotice">
        This view uses current trip data while connected. It keeps the last visible screen after a refresh error but does not cache API traffic or sync offline edits. Download a static snapshot for offline use.
      </p>
      {error ? (
        <div className="travelModeError" role="alert">
          <p>{error}</p>
          <button className="secondary compact" type="button" onClick={() => void refresh()}>Retry refresh</button>
        </div>
      ) : null}
      <section className="travelModeControls" aria-label="On-demand transfer estimates">
        <label>
          Travel mode
                <select value={mode} disabled={pendingDay !== null} onChange={(event) => {
                  estimateGeneration.current += 1;
                  setEstimates({});
                  setMode(event.target.value as LogisticsMode);
                }}>
            <option value="walk">Walking</option>
            <option value="drive">Driving</option>
            <option value="bicycle">Bicycle</option>
            <option value="transit">Transit estimate</option>
          </select>
        </label>
        <label>
          Transfer buffer
                <select value={buffer} disabled={pendingDay !== null} onChange={(event) => {
                  estimateGeneration.current += 1;
                  setEstimates({});
                  setBuffer(Number(event.target.value));
                }}>
            {[0, 5, 10, 15, 20, 30].map((value) => <option key={value} value={value}>{value} minutes</option>)}
          </select>
        </label>
        <p className="formHint">Route estimates run only when requested. They are not saved into the trip.</p>
      </section>
      <div className="travelModeDays">
        {trip.days.map((day) => {
          const estimateForDay = estimates[day.id];
          return (
            <section className="travelModeDay" key={day.id}>
              <header>
                <div>
                  <p className="eyebrow">DAY {day.day_index}</p>
                  <h2>{displayDate(day.date)}</h2>
                  {day.title ? <p className="muted">{day.title}</p> : null}
                </div>
                <button className="secondary compact" type="button" onClick={() => void estimate(day.id)} disabled={pendingDay !== null}>
                  {pendingDay === day.id ? "Estimating…" : "Check transfers"}
                </button>
              </header>
              {day.items.length === 0 ? <p className="emptyText">No itinerary items for this day.</p> : (
                <ol className="travelModeItems">
                  {day.items.filter((item) => item.status !== "cancelled").map((item) => {
                    const reservation = item.reservation
                      ? reservationById.get(item.reservation.id)
                      : null;
                    const conflicts = reservations.flatMap((current) =>
                      current.conflicts
                        .filter((conflict) => conflict.item_id === item.id)
                        .map((conflict) => ({ reservation: current, conflict })),
                    );
                    const linkedAttachments = reservation
                      ? attachmentByReservation.get(reservation.id) ?? []
                      : [];
                    return (
                      <li className="travelModeItem" key={item.id}>
                        <time>{scheduleText(item.start_time, item.end_time)}</time>
                        <div>
                          <h3>{item.title}</h3>
                          <p className="muted">{[item.item_type, item.place?.name, item.place?.address].filter(Boolean).join(" · ")}</p>
                          <PlaceSource place={item.place} />
                          {item.notes ? <p className="travelModeNotes">{item.notes}</p> : null}
                          {reservation ? (
                            <div className="travelReservationAnchor">
                              <strong>{reservation.provider_name}</strong>
                              <span>{reservation.reservation_type} · {reservation.status}</span>
                              <span>{reservationSchedule(reservation)}{reservation.confirmation_code ? ` · ${reservation.confirmation_code}` : ""}</span>
                              {reservation.place ? <span>{reservation.place.name}{reservation.place.address ? ` · ${reservation.place.address}` : ""}</span> : null}
                              <PlaceSource place={reservation.place} />
                              {linkedAttachments.map((attachment) => attachment.download_available ? (
                                <a key={attachment.id} href={travelApi.attachmentDownloadUrl(trip.id, attachment.id)}>
                                  Download {attachment.display_filename}
                                </a>
                              ) : null)}
                            </div>
                          ) : null}
                          {conflicts.map(({ reservation: conflictedReservation, conflict }) => (
                            <p className="travelWarning" key={`${conflictedReservation.id}-${conflict.item_id}`}>
                              Conflict: {conflictedReservation.provider_name} overlaps this stop.
                            </p>
                          ))}
                        </div>
                      </li>
                    );
                  })}
                </ol>
              )}
              {estimateForDay ? (
                <div className="travelTransferList">
                  <p className="travelEstimateMeta">{estimateForDay.mode} estimate · generated {new Date(estimateForDay.generated_at).toLocaleTimeString()}</p>
                  {estimateForDay.legs.length === 0 ? <p className="emptyText">No consecutive scheduled stops with coordinates were found.</p> : estimateForDay.legs.map((leg) => (
                    <p className={leg.warning ? "travelWarning" : "travelTransfer"} key={`${leg.origin_item_id}-${leg.destination_item_id}`}>
                      {leg.origin_title} → {leg.destination_title}: about {formatDuration(leg.duration_seconds)}; {leg.warning ? "tight transfer" : `${formatDuration(leg.available_gap_seconds)} available`}.
                    </p>
                  ))}
                </div>
              ) : null}
            </section>
          );
        })}
      </div>
      {reservationsWithoutVisibleAnchor.length > 0 ? (
        <section className="travelModeDay">
          <p className="eyebrow">RESERVATIONS WITHOUT A VISIBLE ITINERARY ANCHOR</p>
          <ul className="travelDocumentList">
            {reservationsWithoutVisibleAnchor.map((reservation) => (
              <li key={reservation.id}>
                <span>
                  <strong>{reservation.provider_name}</strong> · {reservation.reservation_type} · {reservation.status}
                  <br />
                  {reservationSchedule(reservation)}
                  {reservation.place ? ` · ${reservation.place.name}${reservation.place.address ? `, ${reservation.place.address}` : ""}` : ""}
                  {reservation.confirmation_code ? ` · Confirmation ${reservation.confirmation_code}` : ""}
                  {reservation.place ? <><br /><PlaceSource place={reservation.place} /></> : null}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {tripAttachmentsEnabled ? (
        <section className="travelModeDay">
          <p className="eyebrow">TRIP DOCUMENTS</p>
          <h2>Downloads</h2>
          {!attachmentsAvailable ? <p className="formHint" role="status">The document store is unavailable. Trip details remain available; retry the refresh to check documents again.</p> : null}
          {attachmentsAvailable && attachments.length === 0 ? <p className="emptyText">No trip documents have been added.</p> : null}
          {attachmentsAvailable && attachments.length > 0 ? (
            <ul className="travelDocumentList">
              {attachments.map((attachment) => (
                <li key={attachment.id}>
                  <span>{attachment.display_filename}{attachment.reservation_id && reservationById.has(attachment.reservation_id) ? ` · linked to ${reservationById.get(attachment.reservation_id)!.provider_name}` : ""}{attachment.expires_at ? ` · expires ${attachment.expires_at}` : ""}</span>
                  {attachment.download_available ? <a href={travelApi.attachmentDownloadUrl(trip.id, attachment.id)}>Download</a> : <span className="formError">Unavailable</span>}
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}
      <footer className="travelModeFooter">
        <span>Current trip revision {trip.revision}</span>
        <Link href={`/trips/${trip.id}`}>Return to editing workspace</Link>
      </footer>
    </main>
  );
}
