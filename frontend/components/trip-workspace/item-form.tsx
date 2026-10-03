"use client";

import type { FormEvent } from "react";
import { useState } from "react";
import { errorMessage } from "../../lib/errors";
import type { CreateItemInput, ItemStatus, ItemType, ItineraryItem, PlaceSummary, Reservation, TripDetail, UpdateItemInput } from "../../lib/api";

const itemTypes: ItemType[] = ["activity", "food", "lodging", "transport", "flight", "note"];
const itemStatuses: ItemStatus[] = ["tentative", "planned", "booked", "completed", "cancelled"];
type ItemFormProps = {
  trip: TripDetail;
  dayId: string;
  places: PlaceSummary[];
  reservations: Reservation[];
  initial?: ItineraryItem;
  pending: boolean;
  disabled: boolean;
  onSubmit: (input: CreateItemInput | UpdateItemInput) => Promise<boolean>;
  onCreatePlace: (name: string) => Promise<PlaceSummary>;
  onCancel?: () => void;
};

export default function ItemForm({
  trip,
  dayId,
  places,
  reservations,
  initial,
  pending,
  disabled,
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
  const [reservationId, setReservationId] = useState(initial?.reservation?.id ?? "");
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
      const saved = await onSubmit({
        item_type: itemType,
        title: title.trim(),
        notes: notes.trim() || null,
        start_time: startTime || null,
        end_time: endTime || null,
        status,
        place_id: placeId || null,
        reservation_id: reservationId || null,
      });
      if (!saved) {
        setFormError("The change was not saved. Review the error above and try again.");
      }
    } catch (error) {
      setFormError(errorMessage(error));
    }
  };

  const dayNumber = trip.days.find((day) => day.id === dayId)?.day_index;

  return (
    <form className="itemForm" onSubmit={submit}>
      <fieldset className="itemFields" disabled={disabled || pending || placePending}>
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
          <label>
            Reservation anchor
            <select value={reservationId} onChange={(event) => setReservationId(event.target.value)}>
              <option value="">No reservation</option>
              {reservations.map((reservation) => (
                <option key={reservation.id} value={reservation.id}>
                  {reservation.provider_name} · {reservation.status}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label>
          Notes
          <textarea maxLength={10000} value={notes} onChange={(event) => setNotes(event.target.value)} rows={2} />
        </label>
        <div className="inlineForm">
          <label className="grow">
            Quick-create place
            <input maxLength={240} value={newPlaceName} onChange={(event) => setNewPlaceName(event.target.value)} placeholder="Optional place name" />
          </label>
          <button className="secondary compact" type="button" onClick={createPlace} disabled={!newPlaceName.trim()}>
            {placePending ? "Adding…" : "Add place"}
          </button>
        </div>
        <div className="formActions">
          <button className="primary" type="submit">
            {pending ? "Saving…" : initial ? "Save item" : `Add to day ${dayNumber ?? ""}`}
          </button>
          {onCancel ? <button className="secondary" type="button" onClick={onCancel}>Cancel</button> : null}
        </div>
      </fieldset>
      {formError ? <p className="formError" role="alert">{formError}</p> : null}
    </form>
  );
}

