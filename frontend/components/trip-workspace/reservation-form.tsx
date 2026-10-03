"use client";

import type { FormEvent } from "react";
import { useState } from "react";
import { errorMessage } from "../../lib/errors";
import type { CreateReservationInput, PlaceSummary, Reservation, ReservationStatus, ReservationType, UpdateReservationInput } from "../../lib/api";

const reservationTypes: ReservationType[] = [
  "lodging",
  "flight",
  "train",
  "car_rental",
  "activity",
  "dining",
  "other",
];
const reservationStatuses: ReservationStatus[] = ["tentative", "confirmed", "cancelled"];

export default function ReservationForm({
  places,
  initial,
  pending,
  disabled,
  onSubmit,
  onCancel,
}: {
  places: PlaceSummary[];
  initial?: Reservation;
  pending: boolean;
  disabled: boolean;
  onSubmit: (input: CreateReservationInput | UpdateReservationInput) => Promise<boolean>;
  onCancel?: () => void;
}) {
  const [reservationType, setReservationType] = useState<ReservationType>(initial?.reservation_type ?? "other");
  const [status, setStatus] = useState<ReservationStatus>(initial?.status ?? "tentative");
  const [providerName, setProviderName] = useState(initial?.provider_name ?? "");
  const [confirmationCode, setConfirmationCode] = useState(initial?.confirmation_code ?? "");
  const [startDate, setStartDate] = useState(initial?.start_date ?? "");
  const [startTime, setStartTime] = useState(initial?.start_time ?? "");
  const [endDate, setEndDate] = useState(initial?.end_date ?? "");
  const [endTime, setEndTime] = useState(initial?.end_time ?? "");
  const [placeId, setPlaceId] = useState(initial?.place?.id ?? "");
  const [sourceReference, setSourceReference] = useState(initial?.source_reference ?? "");
  const [notes, setNotes] = useState(initial?.notes ?? "");
  const [formError, setFormError] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!providerName.trim()) {
      setFormError("Add the booking provider or venue name.");
      return;
    }
    setFormError(null);
    try {
      const saved = await onSubmit({
        reservation_type: reservationType,
        status,
        provider_name: providerName.trim(),
        confirmation_code: confirmationCode.trim() || null,
        start_date: startDate || null,
        start_time: startTime || null,
        end_date: endDate || null,
        end_time: endTime || null,
        place_id: placeId || null,
        source_reference: sourceReference.trim() || null,
        notes: notes.trim() || null,
      });
      if (!saved) setFormError("Review the workspace message before retrying this reservation.");
    } catch (error) {
      setFormError(errorMessage(error));
    }
  };

  return (
    <form className="reservationForm" onSubmit={submit}>
      <fieldset disabled={disabled || pending}>
        <div className="formGrid">
          <label>
            Provider or venue
            <input value={providerName} onChange={(event) => setProviderName(event.target.value)} maxLength={200} required placeholder="JR Rail, Hotel name, restaurant…" />
          </label>
          <label>
            Reservation type
            <select value={reservationType} onChange={(event) => setReservationType(event.target.value as ReservationType)}>
              {reservationTypes.map((value) => <option key={value} value={value}>{value.replace("_", " ")}</option>)}
            </select>
          </label>
          <label>
            Booking state
            <select value={status} onChange={(event) => setStatus(event.target.value as ReservationStatus)}>
              {reservationStatuses.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <label>
            Confirmation code
            <input value={confirmationCode} onChange={(event) => setConfirmationCode(event.target.value)} maxLength={160} placeholder="Optional" />
          </label>
          <label>
            Start date
            <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
          </label>
          <label>
            Start time
            <input type="time" value={startTime} onChange={(event) => setStartTime(event.target.value)} />
          </label>
          <label>
            End date
            <input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
          </label>
          <label>
            End time
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
            Source or booking link
            <input value={sourceReference} onChange={(event) => setSourceReference(event.target.value)} maxLength={500} placeholder="Optional URL or reference" />
          </label>
        </div>
        <label>
          Notes
          <textarea maxLength={10000} value={notes} onChange={(event) => setNotes(event.target.value)} rows={2} placeholder="Check-in details, seat, cancellation terms…" />
        </label>
        <p className="formHint">Scheduled times use the trip timezone. Leave all four schedule fields blank for an unscheduled reservation.</p>
        <div className="formActions">
          <button className="primary" type="submit">{pending ? "Saving…" : initial ? "Save reservation" : "Add reservation"}</button>
          {onCancel ? <button className="secondary" type="button" onClick={onCancel}>Cancel</button> : null}
        </div>
      </fieldset>
      {formError ? <p className="formError" role="alert">{formError}</p> : null}
    </form>
  );
}
