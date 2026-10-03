"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { FormEvent } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  type CreateItemInput,
  type ItemStatus,
  type ItemType,
  type ItineraryItem,
  type PlaceSummary,
  type TripDetail,
  type UpdateItemInput,
  travelApi,
} from "../lib/api";

const itemTypes: ItemType[] = ["activity", "food", "lodging", "transport", "flight", "note"];
const itemStatuses: ItemStatus[] = ["tentative", "planned", "booked", "completed", "cancelled"];

function formatDate(value: string, timezone: string): string {
  return new Intl.DateTimeFormat("en", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: timezone,
  }).format(new Date(`${value}T12:00:00Z`));
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Try again.";
}

type ItemFormProps = {
  trip: TripDetail;
  dayId: string;
  places: PlaceSummary[];
  initial?: ItineraryItem;
  pending: boolean;
  onSubmit: (input: CreateItemInput | UpdateItemInput) => Promise<void>;
  onCreatePlace: (name: string) => Promise<PlaceSummary>;
  onCancel?: () => void;
};

function ItemForm({
  trip,
  dayId,
  places,
  initial,
  pending,
  onSubmit,
  onCreatePlace,
  onCancel,
}: ItemFormProps) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [itemType, setItemType] = useState<ItemType>(initial?.item_type ?? "activity");
  const [status, setStatus] = useState<ItemStatus>(initial?.status ?? "tentative");
  const [startTime, setStartTime] = useState(initial?.start_time ?? "");
  const [endTime, setEndTime] = useState(initial?.end_time ?? "");
  const [notes, setNotes] = useState(initial?.notes ?? "");
  const [placeId, setPlaceId] = useState(initial?.place?.id ?? "");
  const [newPlaceName, setNewPlaceName] = useState("");
  const [placePending, setPlacePending] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const createPlace = async () => {
    if (!newPlaceName.trim()) return;
    setPlacePending(true);
    setFormError(null);
    try {
      const place = await onCreatePlace(newPlaceName.trim());
      setPlaceId(place.id);
      setNewPlaceName("");
    } catch (error) {
      setFormError(errorMessage(error));
    } finally {
      setPlacePending(false);
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!title.trim()) {
      setFormError("Give this itinerary item a title.");
      return;
    }
    setFormError(null);
    try {
      await onSubmit({
        item_type: itemType,
        title: title.trim(),
        notes: notes.trim() || null,
        start_time: startTime || null,
        end_time: endTime || null,
        status,
        place_id: placeId || null,
      });
    } catch (error) {
      setFormError(errorMessage(error));
    }
  };

  const dayNumber = trip.days.find((day) => day.id === dayId)?.day_index;

  return (
    <form className="itemForm" onSubmit={submit}>
      <div className="formGrid">
        <label>
          Title
          <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={240} required />
        </label>
        <label>
          Type
          <select value={itemType} onChange={(event) => setItemType(event.target.value as ItemType)}>
            {itemTypes.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label>
          Status
          <select value={status} onChange={(event) => setStatus(event.target.value as ItemStatus)}>
            {itemStatuses.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label>
          Start
          <input type="time" value={startTime} onChange={(event) => setStartTime(event.target.value)} />
        </label>
        <label>
          End
          <input type="time" value={endTime} onChange={(event) => setEndTime(event.target.value)} />
        </label>
        <label>
          Place
          <select value={placeId} onChange={(event) => setPlaceId(event.target.value)}>
            <option value="">No place</option>
            {places.map((place) => <option key={place.id} value={place.id}>{place.name}</option>)}
          </select>
        </label>
      </div>
      <label>
        Notes
        <textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows={2} />
      </label>
      <div className="inlineForm">
        <label className="grow">
          Quick-create place
          <input value={newPlaceName} onChange={(event) => setNewPlaceName(event.target.value)} placeholder="Optional place name" />
        </label>
        <button className="secondary compact" type="button" onClick={createPlace} disabled={placePending || pending || !newPlaceName.trim()}>
          {placePending ? "Adding…" : "Add place"}
        </button>
      </div>
      {formError ? <p className="formError" role="alert">{formError}</p> : null}
      <div className="formActions">
        <button className="primary" type="submit" disabled={pending || placePending}>
          {pending ? "Saving…" : initial ? "Save item" : `Add to day ${dayNumber ?? ""}`}
        </button>
        {onCancel ? <button className="secondary" type="button" onClick={onCancel} disabled={pending}>Cancel</button> : null}
      </div>
    </form>
  );
}

function DayTitleForm({
  dayId,
  title,
  pending,
  onSave,
}: {
  dayId: string;
  title: string | null;
  pending: boolean;
  onSave: (title: string | null) => Promise<void>;
}) {
  const [value, setValue] = useState(title ?? "");
  return (
    <form className="dayTitleForm" onSubmit={(event) => { event.preventDefault(); void onSave(value.trim() || null); }}>
      <label className="srOnly" htmlFor={`day-title-${dayId}`}>Day title</label>
      <input id={`day-title-${dayId}`} value={value} onChange={(event) => setValue(event.target.value)} placeholder="Add a day title" maxLength={200} />
      <button className="iconButton" type="submit" disabled={pending} aria-label="Save day title">Save</button>
    </form>
  );
}

export default function TripWorkspace({ tripId }: { tripId: string }) {
  const router = useRouter();
  const [trip, setTrip] = useState<TripDetail | null>(null);
  const [places, setPlaces] = useState<PlaceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [editingItem, setEditingItem] = useState<string | null>(null);
  const [addingDay, setAddingDay] = useState<string | null>(null);
  const [tripForm, setTripForm] = useState({ title: "", start_date: "", end_date: "", timezone: "UTC" });
  const [showTripEditor, setShowTripEditor] = useState(false);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [nextTrip, nextPlaces] = await Promise.all([travelApi.getTrip(tripId), travelApi.listPlaces()]);
      setTrip(nextTrip);
      setPlaces(nextPlaces);
      setTripForm({ title: nextTrip.title, start_date: nextTrip.start_date, end_date: nextTrip.end_date, timezone: nextTrip.timezone });
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setLoading(false);
    }
  }, [tripId]);

  useEffect(() => {
    void Promise.resolve().then(refresh);
  }, [refresh]);

  const run = async (key: string, operation: () => Promise<void>) => {
    setPending(key);
    setError(null);
    try {
      await operation();
      await refresh();
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setPending(null);
    }
  };

  const createPlace = async (name: string) => {
    const place = await travelApi.createPlace({ name });
    setPlaces((current) => [...current, place].sort((left, right) => left.name.localeCompare(right.name)));
    return place;
  };

  const deleteTrip = () => {
    if (!window.confirm("Permanently delete this trip and all of its itinerary items?")) return;
    void run("trip-delete", async () => {
      await travelApi.deleteTrip(tripId);
      router.push("/");
    });
  };

  const dayCountLabel = useMemo(
    () => trip ? `${trip.days.length} ${trip.days.length === 1 ? "day" : "days"}` : "",
    [trip],
  );

  if (loading) return <main className="centerState"><p>Loading itinerary…</p></main>;
  if (!trip) return <main className="centerState"><p className="formError" role="alert">{error ?? "Trip not found."}</p><Link className="primary linkButton" href="/">Back to trips</Link></main>;

  const saveTrip = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void run("trip", async () => {
      await travelApi.updateTrip(tripId, tripForm);
      setShowTripEditor(false);
    });
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
          <a className="active" href="#itinerary">Itinerary</a>
          <a href="#places">Places</a>
          <span className="navDisabled">Map <small>Later</small></span>
          <span className="navDisabled">Reservations <small>Later</small></span>
        </nav>
        <div className="status"><span className="dot" />Local PostgreSQL planner</div>
      </aside>

      <section className="content" id="itinerary">
        <header className="topbar">
          <div>
            <p className="eyebrow">{trip.timezone} · {dayCountLabel}</p>
            <h2>Day-by-day itinerary</h2>
            <p className="muted">Manual changes are authoritative and do not require AI availability.</p>
          </div>
          <div className="topActions">
            <button className="secondary" type="button" onClick={() => setShowTripEditor((current) => !current)}>{showTripEditor ? "Close trip editor" : "Edit trip"}</button>
            <button className="danger" type="button" onClick={deleteTrip} disabled={pending !== null}>Delete trip</button>
          </div>
        </header>

        {error ? <p className="errorBanner" role="alert">{error}</p> : null}

        {showTripEditor ? (
          <form className="panel tripEditor" onSubmit={saveTrip}>
            <div className="formGrid">
              <label>Trip title<input value={tripForm.title} onChange={(event) => setTripForm({ ...tripForm, title: event.target.value })} required /></label>
              <label>Timezone<input value={tripForm.timezone} onChange={(event) => setTripForm({ ...tripForm, timezone: event.target.value })} placeholder="America/Los_Angeles" required /></label>
              <label>Start date<input type="date" value={tripForm.start_date} onChange={(event) => setTripForm({ ...tripForm, start_date: event.target.value })} required /></label>
              <label>End date<input type="date" value={tripForm.end_date} onChange={(event) => setTripForm({ ...tripForm, end_date: event.target.value })} required /></label>
            </div>
            <button className="primary" type="submit" disabled={pending !== null}>{pending === "trip" ? "Saving…" : "Save trip"}</button>
          </form>
        ) : null}

        <div className="days">
          {trip.days.map((day) => (
            <article className="day" key={day.id}>
              <div className="dayHeading">
                <div>
                  <p className="date">Day {day.day_index} · {formatDate(day.date, trip.timezone)}</p>
                  <DayTitleForm key={`${day.id}-${day.title ?? ""}`} dayId={day.id} title={day.title} pending={pending === `day-${day.id}`} onSave={(title) => run(`day-${day.id}`, async () => { await travelApi.updateDay(trip.id, day.id, title); })} />
                </div>
                <button className="secondary compact" type="button" onClick={() => setAddingDay((current) => current === day.id ? null : day.id)}>{addingDay === day.id ? "Close form" : "+ Add item"}</button>
              </div>
              <div className="items">
                {day.items.length === 0 ? <p className="emptyText">Nothing planned yet.</p> : null}
                {day.items.map((item, index) => (
                  <div className="itemCard" key={item.id}>
                    <div className="itemSummary">
                      <div className="itemTime">{item.start_time ?? "—"}{item.end_time ? `–${item.end_time}` : ""}</div>
                      <div className="itemBody">
                        <div className="itemTitle"><strong>{item.title}</strong><span className={`badge badge-${item.status}`}>{item.status}</span></div>
                        <p className="itemMeta">{item.item_type}{item.place ? ` · ${item.place.name}` : ""}</p>
                        {item.notes ? <p className="itemNotes">{item.notes}</p> : null}
                      </div>
                      <div className="itemActions">
                        <button className="iconButton" type="button" onClick={() => setEditingItem((current) => current === item.id ? null : item.id)} aria-expanded={editingItem === item.id}>{editingItem === item.id ? "Close" : "Edit"}</button>
                        <button className="iconButton" type="button" onClick={() => void run(`move-${item.id}`, () => travelApi.moveItem(trip.id, item.id, day.id, Math.max(index - 1, 0)).then(() => undefined))} disabled={pending !== null || index === 0} aria-label="Move item up">↑</button>
                        <button className="iconButton" type="button" onClick={() => void run(`move-${item.id}`, () => travelApi.moveItem(trip.id, item.id, day.id, index + 1).then(() => undefined))} disabled={pending !== null || index === day.items.length - 1} aria-label="Move item down">↓</button>
                        <select className="moveSelect" defaultValue="" onChange={(event) => { const destination = trip.days.find((candidate) => candidate.id === event.target.value); if (destination) void run(`move-${item.id}`, () => travelApi.moveItem(trip.id, item.id, destination.id, destination.items.length).then(() => undefined)); event.currentTarget.value = ""; }} disabled={pending !== null} aria-label="Move item to another day"><option value="">Move to…</option>{trip.days.filter((candidate) => candidate.id !== day.id).map((candidate) => <option key={candidate.id} value={candidate.id}>Day {candidate.day_index}</option>)}</select>
                        <button className="iconButton dangerText" type="button" onClick={() => { if (window.confirm(`Delete ${item.title}?`)) void run(`delete-${item.id}`, async () => { await travelApi.deleteItem(trip.id, item.id); }); }} disabled={pending !== null} aria-label={`Delete ${item.title}`}>Delete</button>
                      </div>
                    </div>
                    {editingItem === item.id ? <ItemForm trip={trip} dayId={day.id} places={places} initial={item} pending={pending === `item-${item.id}`} onSubmit={(input) => run(`item-${item.id}`, async () => { await travelApi.updateItem(trip.id, item.id, input); setEditingItem(null); })} onCreatePlace={createPlace} onCancel={() => setEditingItem(null)} /> : null}
                  </div>
                ))}
              </div>
              {addingDay === day.id ? <ItemForm trip={trip} dayId={day.id} places={places} pending={pending === `add-${day.id}`} onSubmit={(input) => run(`add-${day.id}`, async () => { await travelApi.createItem(trip.id, day.id, input as CreateItemInput); setAddingDay(null); })} onCreatePlace={createPlace} /> : null}
            </article>
          ))}
        </div>
      </section>

      <aside className="aiPanel" id="places">
        <div><p className="eyebrow">TRIP CONTEXT</p><h2>Places</h2><p className="muted">Manual place records can be attached to itinerary items. Maps and external providers are intentionally deferred.</p></div>
        <div className="placeList">{places.length === 0 ? <p className="emptyText">No places yet.</p> : places.map((place) => <div className="placeRow" key={place.id}><strong>{place.name}</strong>{place.address ? <span>{place.address}</span> : null}</div>)}</div>
        <div className="aiBoundary"><span>Planned boundary</span><code>travel-api → personal-ai-system</code><p>AI research will remain a separate, evidence-backed workflow.</p></div>
      </aside>
    </main>
  );
}
