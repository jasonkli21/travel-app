"use client";

import { useState } from "react";
import type { CreateItemInput, PlaceSummary, Reservation, TripDetail } from "../../lib/api";
import { travelApi } from "../../lib/api";
import DayTitleForm from "./day-title-form";
import ItemForm from "./item-form";
import PlaceAttribution from "./place-attribution";
import { formatDate } from "./formatters";

type RunWorkspaceMutation = (key: string, operation: () => Promise<void>) => Promise<boolean>;

export default function ItinerarySection({
  trip,
  places,
  reservations,
  pending,
  stale,
  editorGeneration,
  runMutation,
  onCreatePlace,
}: {
  trip: TripDetail;
  places: PlaceSummary[];
  reservations: Reservation[];
  pending: string | null;
  stale: boolean;
  editorGeneration: number;
  runMutation: RunWorkspaceMutation;
  onCreatePlace: (name: string) => Promise<PlaceSummary>;
}) {
  const [editingItem, setEditingItem] = useState<string | null>(null);
  const [addingDay, setAddingDay] = useState<string | null>(null);
  const disabled = pending !== null || stale;

  return (
    <section className="itinerarySection" id="itinerary">
      <div className="sectionHeading"><div><p className="eyebrow">AUTHORITATIVE PLAN</p><h2>Day-by-day itinerary</h2></div></div>
      <div className="days">
        {trip.days.map((day) => (
          <article className="day" key={day.id}>
            <div className="dayHeading">
              <div>
                <p className="date">Day {day.day_index} · {formatDate(day.date)}</p>
                <DayTitleForm
                  key={`${editorGeneration}-${day.id}-${day.title ?? ""}`}
                  dayId={day.id}
                  title={day.title}
                  pending={pending === `day-${day.id}`}
                  disabled={disabled}
                  onSave={(title) => runMutation(`day-${day.id}`, async () => {
                    await travelApi.updateDay(trip.id, day.id, title, trip.revision);
                  })}
                />
              </div>
              <button className="secondary compact" type="button" onClick={() => setAddingDay((current) => current === day.id ? null : day.id)} disabled={disabled}>{addingDay === day.id ? "Close form" : "+ Add item"}</button>
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
                      <button className="iconButton" type="button" onClick={() => setEditingItem((current) => current === item.id ? null : item.id)} disabled={disabled} aria-expanded={editingItem === item.id}>{editingItem === item.id ? "Close" : "Edit"}</button>
                      <button className="iconButton" type="button" onClick={() => void runMutation(`move-${item.id}`, () => travelApi.moveItem(trip.id, item.id, day.id, Math.max(index - 1, 0), trip.revision).then(() => undefined))} disabled={disabled || index === 0} aria-label="Move item up">↑</button>
                      <button className="iconButton" type="button" onClick={() => void runMutation(`move-${item.id}`, () => travelApi.moveItem(trip.id, item.id, day.id, index + 1, trip.revision).then(() => undefined))} disabled={disabled || index === day.items.length - 1} aria-label="Move item down">↓</button>
                      <select className="moveSelect" defaultValue="" onChange={(event) => { const destination = trip.days.find((candidate) => candidate.id === event.target.value); if (destination) void runMutation(`move-${item.id}`, () => travelApi.moveItem(trip.id, item.id, destination.id, destination.items.length, trip.revision).then(() => undefined)); event.currentTarget.value = ""; }} disabled={disabled} aria-label="Move item to another day"><option value="">Move to…</option>{trip.days.filter((candidate) => candidate.id !== day.id).map((candidate) => <option key={candidate.id} value={candidate.id}>Day {candidate.day_index}</option>)}</select>
                      <button className="iconButton dangerText" type="button" onClick={() => { if (window.confirm(`Delete ${item.title}?`)) void runMutation(`delete-${item.id}`, async () => { await travelApi.deleteItem(trip.id, item.id, trip.revision); }); }} disabled={disabled} aria-label={`Delete ${item.title}`}>Delete</button>
                    </div>
                  </div>
                  {editingItem === item.id ? <ItemForm key={`${editorGeneration}-${item.id}`} trip={trip} dayId={day.id} places={places} reservations={reservations} initial={item} pending={pending === `item-${item.id}`} disabled={disabled} onSubmit={async (input, expectedTripRevision) => { const saved = await runMutation(`item-${item.id}`, async () => { await travelApi.updateItem(trip.id, item.id, input, expectedTripRevision); }); if (saved) setEditingItem(null); return saved; }} onCreatePlace={onCreatePlace} onCancel={() => setEditingItem(null)} /> : null}
                </div>
              ))}
            </div>
            {addingDay === day.id ? <ItemForm key={`${editorGeneration}-new-${day.id}`} trip={trip} dayId={day.id} places={places} reservations={reservations} pending={pending === `add-${day.id}`} disabled={disabled} onSubmit={async (input, expectedTripRevision) => { const saved = await runMutation(`add-${day.id}`, async () => { await travelApi.createItem(trip.id, day.id, input as CreateItemInput, expectedTripRevision); }); if (saved) setAddingDay(null); return saved; }} onCreatePlace={onCreatePlace} onCancel={() => setAddingDay(null)} /> : null}
          </article>
        ))}
      </div>
    </section>
  );
}
