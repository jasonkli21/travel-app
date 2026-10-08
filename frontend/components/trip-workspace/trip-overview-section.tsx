"use client";

import type { FormEvent } from "react";
import { useState } from "react";
import Link from "next/link";
import type { TripDetail, UpdateTripInput } from "../../lib/api";

type TripSettingsValues = Pick<TripDetail, "title" | "start_date" | "end_date" | "timezone">;

function settingsFromTrip(trip: TripDetail): TripSettingsValues {
  return {
    title: trip.title,
    start_date: trip.start_date,
    end_date: trip.end_date,
    timezone: trip.timezone,
  };
}

export default function TripOverviewSection({
  trip,
  pending,
  saving,
  error,
  stale,
  confirmedReservations,
  tentativeReservations,
  savedPlaceCount,
  conflictCount,
  onUpdateTrip,
  onDeleteTrip,
  onReload,
}: {
  trip: TripDetail;
  pending: boolean;
  saving: boolean;
  error: string | null;
  stale: boolean;
  confirmedReservations: number;
  tentativeReservations: number;
  savedPlaceCount: number;
  conflictCount: number;
  onUpdateTrip: (input: UpdateTripInput, onCommitted: () => void) => void;
  onDeleteTrip: () => void;
  onReload: () => void;
}) {
  const [showTripEditor, setShowTripEditor] = useState(false);
  const [tripFormState, setTripFormState] = useState(() => ({
    trip,
    values: settingsFromTrip(trip),
  }));
  if (tripFormState.trip !== trip) {
    // Adjust this component's form state to each refreshed snapshot while
    // keeping the editor's open/closed state independent.
    setTripFormState({ trip, values: settingsFromTrip(trip) });
  }
  const tripForm = tripFormState.values;
  const setTripForm = (values: TripSettingsValues) => {
    setTripFormState((current) => ({ ...current, values }));
  };

  const saveTrip = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onUpdateTrip(tripForm, () => setShowTripEditor(false));
  };

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">{trip.timezone} · {trip.days.length} {trip.days.length === 1 ? "day" : "days"}</p>
          <h2>Trip overview</h2>
          <p className="muted">Manual changes are authoritative. Reservations and candidates remain useful when AI is offline.</p>
        </div>
        <div className="topActions">
          <Link className="secondary linkButton" href={`/trips/${trip.id}/travel`}>Open travel mode</Link>
          <button className="secondary" type="button" onClick={() => setShowTripEditor((current) => !current)} disabled={pending}>{showTripEditor ? "Close trip editor" : "Edit trip"}</button>
          <button className="danger" type="button" onClick={onDeleteTrip} disabled={pending}>Delete trip</button>
        </div>
      </header>

      {error ? <p className="errorBanner" role="alert">{error}</p> : null}
      {stale ? <button className="secondary" type="button" onClick={onReload}>Reload workspace</button> : null}

      <div className="overviewPanel" aria-label="Trip summary">
        <div className="overviewStat"><strong>{trip.days.length}</strong><span>days</span></div>
        <div className="overviewStat"><strong>{trip.item_count}</strong><span>itinerary items</span></div>
        <div className="overviewStat"><strong>{confirmedReservations}</strong><span>confirmed anchors</span></div>
        <div className="overviewStat"><strong>{tentativeReservations}</strong><span>tentative bookings</span></div>
        <div className="overviewStat"><strong>{savedPlaceCount}</strong><span>saved candidates</span></div>
        <div className={`overviewStat ${conflictCount > 0 ? "overviewWarning" : ""}`}><strong>{conflictCount}</strong><span>active conflicts</span></div>
      </div>

      {showTripEditor ? (
        <form className="panel tripEditor" onSubmit={saveTrip}>
          <fieldset disabled={pending}>
            <div className="formGrid">
              <label>Trip title<input maxLength={200} value={tripForm.title} onChange={(event) => setTripForm({ ...tripForm, title: event.target.value })} required /></label>
              <label>Timezone<input maxLength={64} value={tripForm.timezone} onChange={(event) => setTripForm({ ...tripForm, timezone: event.target.value })} placeholder="America/Los_Angeles" required /></label>
              <label>Start date<input type="date" value={tripForm.start_date} onChange={(event) => setTripForm({ ...tripForm, start_date: event.target.value })} required /></label>
              <label>End date<input type="date" value={tripForm.end_date} onChange={(event) => setTripForm({ ...tripForm, end_date: event.target.value })} required /></label>
            </div>
            <button className="primary" type="submit">{saving ? "Saving…" : "Save trip"}</button>
          </fieldset>
        </form>
      ) : null}
    </>
  );
}
